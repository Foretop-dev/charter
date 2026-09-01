"""Groups charter's per-server and per-(server, tool, capability) `Finding`s into one
`keel.triage.IssueGroup` per MCP server — a server that cannot be introspected becomes one
`review` group with every unresolved capability folded inside it, rather than reading as
several separate alarms for the same underlying "we don't know" fact.

Consumes `ServerSet`/`enumeration`/`drift` directly, the same live objects `cli.py` already
builds before calling `to_findings` — no re-parsing of rendered `Finding` text.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from keel.finding import ProductCode
from keel.triage import IssueGroup, Lane, OccurrenceRef, TriageResult, make_fingerprint

from charter.capability import (
    CapabilityClass,
    classify_tool,
    declared_credential_env_vars,
    has_credential_bearing_argument,
)
from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet
from charter.server_registry import (
    ServerRegistryEntry,
    load_bundled_server_registry,
    lookup_server_package,
)

# The two classes that make a server worth acting on rather than merely recording. Held once as
# CapabilityClass members (for the registry, R18) and once as their string values (for the
# enumerated path, which compares against classify_tool's own string capability values).
_HIGH_RISK_CAPABILITY_CLASSES = frozenset(
    {CapabilityClass.CREDENTIAL_ACCESS, CapabilityClass.CODE_EXECUTION}
)
_HIGH_RISK_CAPABILITIES = {c.value for c in _HIGH_RISK_CAPABILITY_CLASSES}
_UNKNOWN_CAPABILITY = "unknown"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _server_occurrence(server: Server, root: Path) -> OccurrenceRef:
    reach = server.command or server.url or server.name
    return OccurrenceRef(
        finding_identity=make_fingerprint("charter", server.name, "-", "-"),
        source_uri=_relative(server.source_file, root),
        locator=str(server.source_line),
        excerpt=f"{server.name} ({server.transport.value}): {reach}",
        context="server",
    )


def _tool_occurrences(
    server: Server, tools: tuple[Tool, ...], root: Path
) -> tuple[list[OccurrenceRef], set[str]]:
    """One occurrence per (tool, matched capability) — the exact granularity `findings.py`
    already established. Returns the occurrences plus the set of distinct capability values
    seen, so the caller can decide the group's lane without a second pass over the tools."""
    occurrences = []
    capability_values: set[str] = set()
    source_uri = _relative(server.source_file, root)
    for tool in tools:
        classification = classify_tool(tool)
        capabilities: list[CapabilityClass | None] = (
            list(classification.capabilities) if classification.capabilities else [None]
        )
        for capability in capabilities:
            cap_value = capability.value if capability is not None else _UNKNOWN_CAPABILITY
            capability_values.add(cap_value)
            occurrences.append(
                OccurrenceRef(
                    finding_identity=make_fingerprint("charter", server.name, tool.name, cap_value),
                    source_uri=source_uri,
                    locator=str(server.source_line),
                    excerpt=f"{tool.name}: {cap_value}",
                    context=cap_value,
                )
            )
    return occurrences, capability_values


def _static_lane_and_reason(
    entry: ServerRegistryEntry | None,
    credential_env_vars: tuple[str, ...],
    credential_argument: bool,
) -> tuple[Lane, str, str]:
    """The lane for a server that was never launched, from what the config alone establishes.

    Every branch says how it knows. "documented" never means "enumerated": a registry entry
    describes the published package, not the tools this particular server returned, and
    conflating the two would be the false `present` charter's whole posture is built against.
    """
    credential_notes: list[str] = []
    if credential_argument:
        # Never the argument itself — see capability.has_credential_bearing_argument.
        credential_notes.append("a credential is embedded in a launch argument")
    if credential_env_vars:
        credential_notes.append(f"wired to {', '.join(credential_env_vars)}")

    documented = ", ".join(sorted(c.value for c in entry.capabilities)) if entry else ""
    risky = (
        sorted(c.value for c in entry.capabilities & _HIGH_RISK_CAPABILITY_CLASSES) if entry else []
    )

    if risky:
        reason = f"Plan · documented {', '.join(risky)} capability, not enumerated"
        if credential_notes:
            reason += f" · {'; '.join(credential_notes)}"
        return Lane.PLAN, reason, "registry"

    if credential_notes:
        reason = f"Plan · {'; '.join(credential_notes)}"
        if documented:
            reason += f" · documented capabilities: {documented}, not enumerated"
        return Lane.PLAN, reason, "registry" if entry else "config"

    if entry:
        return (
            Lane.INVENTORY,
            f"Inventory · documented capabilities: {documented}, not enumerated, nothing elevated",
            "registry",
        )

    return (
        Lane.REVIEW,
        "Review · not enumerated — static parsing cannot say what tools it exposes",
        "none",
    )


def build_triage(
    server_set: ServerSet,
    enumeration: dict[Server, EnumerationResult],
    drift: tuple[Drift, ...],
    root: Path,
) -> TriageResult:
    new_servers = {d.server_name for d in drift if d.kind is DriftKind.NEW_SERVER}
    drifted_servers = {
        d.server_name
        for d in drift
        if d.kind in (DriftKind.NEW_TOOL, DriftKind.SEVERITY_INCREASED) and d.tool_name
    }
    # Loaded once per run rather than per server — it is small, bundled, and read-only.
    registry = load_bundled_server_registry()

    # R22: one server declared in several client configs is still one server. A team using more
    # than one client repeats each server across each client's file — the real dotCMS/core
    # repository declares `angular-cli` byte-identically in .mcp.json, .cursor/mcp.json and
    # .vscode/mcp.json. Emitting a group per declaration produced three groups carrying the
    # *same* fingerprint, and `hub.models.IssueGroupRow` is unique on (run_id, fingerprint), so
    # such a run would violate that constraint on ingest. Grouping by name folds the
    # declarations into one group with an occurrence each — the same folding this module
    # already does for a server's tools, applied one level up.
    by_name: dict[str, list[Server]] = defaultdict(list)
    for server in server_set.servers:
        by_name[server.name].append(server)

    groups: list[IssueGroup] = []
    for name, declarations in by_name.items():
        # The declaration carrying the most evidence decides the lane, so a credential wired in
        # only one client's config is not lost to whichever file happened to be parsed first.
        # Everything below is computed per declaration and then reduced.
        per_declaration = [
            _declaration_answer(server, enumeration, registry, new_servers, drifted_servers, root)
            for server in declarations
        ]
        best = max(per_declaration, key=lambda answer: _LANE_SEVERITY[answer.lane])
        occurrences = [
            occurrence for answer in per_declaration for occurrence in answer.occurrences
        ]
        capability_values = set().union(*(a.capability_values for a in per_declaration))
        server = best.server
        result = enumeration.get(server)
        lane, reason, capability_source = best.lane, best.reason, best.capability_source

        groups.append(
            IssueGroup(
                fingerprint=make_fingerprint("charter", name),
                product=ProductCode.CHARTER,
                label=name,
                lane=lane,
                reason=reason,
                occurrence_count=len(occurrences),
                context_count=len(capability_values) or 1,
                occurrences=occurrences,
                deadline=None,
                attributes={
                    "transport": server.transport.value,
                    "tool_count": str(len(result.tools)) if result is not None else "-",
                    "capability_source": capability_source,
                    # Only present when it is true, so an ordinary single-declaration server's
                    # attributes are unchanged.
                    **({"declared_in": str(len(declarations))} if len(declarations) > 1 else {}),
                },
            )
        )

    return TriageResult(
        product=ProductCode.CHARTER,
        groups=groups,
        observations=sum(g.occurrence_count for g in groups),
    )


@dataclass(frozen=True)
class _DeclarationAnswer:
    """One config file's answer about one server, before duplicates are reduced."""

    server: Server
    lane: Lane
    reason: str
    capability_source: str
    occurrences: list[OccurrenceRef]
    capability_values: set[str]


_LANE_SEVERITY = {Lane.INVENTORY: 0, Lane.REVIEW: 1, Lane.PLAN: 2, Lane.ACT_NOW: 3}


def _declaration_answer(
    server: Server,
    enumeration: dict[Server, EnumerationResult],
    registry: dict[str, ServerRegistryEntry],
    new_servers: set[str],
    drifted_servers: set[str],
    root: Path,
) -> _DeclarationAnswer:
    result = enumeration.get(server)
    occurrences = [_server_occurrence(server, root)]
    capability_values: set[str] = set()

    if result is not None and result.error is None and result.tools:
        tool_occurrences, capability_values = _tool_occurrences(server, result.tools, root)
        occurrences.extend(tool_occurrences)

    # R18: what the committed config itself establishes, without launching anything. A declared
    # credential and a curated package's documented capabilities are both static facts; only the
    # tool list needs enumeration. `capability_source` records which of the three this answer
    # came from, so a reader is never left guessing whether a capability was observed or merely
    # documented.
    credential_env_vars = declared_credential_env_vars(server)
    credential_argument = has_credential_bearing_argument(server)
    registry_entry = lookup_server_package(server, registry)
    capability_source = "none"

    if server.name in new_servers:
        lane, reason = Lane.ACT_NOW, "Act now · new server since baseline"
    elif server.name in drifted_servers:
        lane, reason = Lane.ACT_NOW, "Act now · capability profile changed since baseline"
    elif result is None:
        lane, reason, capability_source = _static_lane_and_reason(
            registry_entry, credential_env_vars, credential_argument
        )
    elif result.error is not None:
        lane = Lane.REVIEW
        reason = f"Review · enumeration failed: {result.error}"
    elif capability_values & _HIGH_RISK_CAPABILITIES:
        risky = ", ".join(sorted(capability_values & _HIGH_RISK_CAPABILITIES))
        lane, reason = Lane.PLAN, f"Plan · exposes {risky} capability"
        capability_source = "enumeration"
    else:
        tool_count = len(result.tools) if result is not None else 0
        lane = Lane.INVENTORY
        reason = f"Inventory · enumerated, {tool_count} tool(s), no elevated capability"
        capability_source = "enumeration"

    return _DeclarationAnswer(
        server=server,
        lane=lane,
        reason=reason,
        capability_source=capability_source,
        occurrences=occurrences,
        capability_values=capability_values,
    )
