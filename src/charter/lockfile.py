from pathlib import Path

from charter.canonical import canonical_json
from charter.capability import classify_tool
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet

SCHEMA_VERSION = 4
"""specs/charter.md §5: "The lock file format needs a documented, versioned schema published
from day one — third parties will read it." Bump this whenever a field is added, removed, or
its meaning changes. v2 (Session 15, Slice 2 part 1) added `tools`/`enumeration_error` per
server. v3 (Session 15, Slice 2 part 2) added `capabilities`/`severity`/`rule_version` per
tool — DEC-03's rules-only classifier (src/charter/capability.py). v4 structurally removed
raw `args`, which can contain credential values, and replaced them with non-sensitive
`arg_count`."""


def _sort_key(server: Server) -> tuple[str, str]:
    # (name, source_file) as the tie-break — deterministic across repeated runs of the same
    # scan (collect() always resolves root once, so the same repo checkout produces the same
    # absolute paths every time), which is all "byte-stable for unchanged input" requires. The
    # serialized output itself never carries an absolute path — see _server_dict below.
    return (server.name, str(server.source_file))


def _relative_source(server: Server, root: Path) -> str:
    try:
        return str(server.source_file.resolve().relative_to(root))
    except ValueError:
        # Only reachable if a caller hand-built a Server pointing outside root (collect() never
        # does this) — fall back to the absolute path rather than crash on a real-world input
        # collect() itself would never actually produce.
        return str(server.source_file)


def _tool_dict(tool: Tool) -> dict[str, object]:
    classification = classify_tool(tool)
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        # DEC-03: rules-only classification, sorted for the same byte-stability reason
        # canonical.py sorts everything else — a frozenset has no defined iteration order.
        "capabilities": sorted(c.value for c in classification.capabilities),
        "severity": classification.severity.value,
        "rule_version": classification.rule_version,
    }


def _server_dict(
    server: Server, root: Path, enumeration: EnumerationResult | None
) -> dict[str, object]:
    # Three states, same "don't conflate absence of evidence with evidence of absence"
    # discipline telltale's own three-state coverage model uses: `tools: null` means
    # enumeration wasn't attempted for this server (a plain static scan, or --enumerate wasn't
    # passed) — never confused with `tools: []`, which means it WAS attempted and the server
    # genuinely advertised zero tools. `enumeration_error` is set only when an attempt was made
    # and failed.
    tools: list[dict[str, object]] | None = None
    enumeration_error: str | None = None
    if enumeration is not None:
        enumeration_error = enumeration.error
        if enumeration.error is None:
            tools = [_tool_dict(t) for t in enumeration.tools]

    return {
        "name": server.name,
        "transport": server.transport.value,
        "command": server.command,
        # Arguments remain available in memory for explicit live enumeration, but their text
        # never crosses the lock-file boundary: a positional argument can itself be a password,
        # token, or credential-bearing DSN. Counting preserves the useful fact that arguments
        # exist without logging or hash-logging their values (specs/charter.md DEC-06).
        "arg_count": len(server.args),
        "env_var_names": list(server.env_var_names),
        "url": server.url,
        "header_names": list(server.header_names),
        "source_file": _relative_source(server, root),
        "source_line": server.source_line,
        "tools": tools,
        "enumeration_error": enumeration_error,
    }


def to_manifest(
    servers: ServerSet,
    root: Path,
    enumeration: dict[Server, EnumerationResult] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    enumeration = enumeration or {}
    ordered = sorted(servers.servers, key=_sort_key)
    return {
        "schema_version": SCHEMA_VERSION,
        "servers": [_server_dict(s, root, enumeration.get(s)) for s in ordered],
    }


def render_lock(
    servers: ServerSet, root: Path, enumeration: dict[Server, EnumerationResult] | None = None
) -> str:
    return canonical_json(to_manifest(servers, root, enumeration))


def write_lock(
    servers: ServerSet,
    root: Path,
    lock_path: Path,
    enumeration: dict[Server, EnumerationResult] | None = None,
) -> None:
    # write_bytes, never a text-mode handle: on Windows, text-mode writing translates \n to
    # \r\n unless told otherwise, which would break specs/charter.md §6's "LF endings"
    # requirement silently depending on the developer's OS. canonical_json's own \n is already
    # exactly what should land on disk, byte for byte.
    lock_path.write_bytes(render_lock(servers, root, enumeration).encode("utf-8"))
