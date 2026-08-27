from keel.render.markdown import escape_cell

from charter.capability import Severity, classify_tool
from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult
from charter.models import Server, ServerSet

_SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW)


def _tool_rows(server: Server, result: EnumerationResult | None) -> list[str]:
    if result is None:
        return [f"| {escape_cell(server.name)} | {server.transport.value} | _not enumerated_ | | |"]
    if result.error is not None:
        error = escape_cell(result.error)
        return [f"| {escape_cell(server.name)} | {server.transport.value} | _error: {error}_ | | |"]
    if not result.tools:
        return [f"| {escape_cell(server.name)} | {server.transport.value} | _no tools_ | | |"]

    rows = []
    for tool in sorted(result.tools, key=lambda t: t.name):
        classification = classify_tool(tool)
        capabilities = ", ".join(sorted(c.value for c in classification.capabilities)) or "unknown"
        rows.append(
            f"| {escape_cell(server.name)} | {server.transport.value} | {escape_cell(tool.name)} | "
            f"{capabilities} | {classification.severity.value} |"
        )
    return rows


def _summary_line(servers: ServerSet, enumeration: dict[Server, EnumerationResult]) -> str:
    counts = dict.fromkeys(_SEVERITY_ORDER, 0)
    tool_count = 0
    for result in enumeration.values():
        if result.error is not None:
            continue
        for tool in result.tools:
            counts[classify_tool(tool).severity] += 1
            tool_count += 1

    parts = ", ".join(f"{counts[s]} {s.value}" for s in _SEVERITY_ORDER)
    return f"**{len(servers.servers)} server(s), {tool_count} tool(s)** — {parts}"


def _drift_rows(drift: tuple[Drift, ...]) -> list[str]:
    rows = []
    for d in drift:
        if d.kind is DriftKind.NEW_SERVER:
            what, before, after = f"**{d.server_name}** (new server)", "—", "—"
        elif d.kind is DriftKind.NEW_TOOL:
            what = f"{d.server_name} / {d.tool_name}"
            before, after = "—", d.after_severity or ""
        else:
            what = f"{d.server_name} / {d.tool_name}"
            before, after = d.before_severity or "", d.after_severity or ""
        rows.append(f"| {escape_cell(what)} | {d.kind.value} | {before} | {after} |")
    return rows


def render_markdown(
    servers: ServerSet,
    enumeration: dict[Server, EnumerationResult] | None = None,
    drift: tuple[Drift, ...] = (),
) -> str:
    """PR-comment-ready snapshot of the current scan (mirrors apps/telltale/src/telltale/render/
    markdown.py's role for this app's own domain model). The server/tool table is always a
    point-in-time snapshot, never a diff. `drift` (charter/drift.py, populated only when `--base`
    was passed) is the actual diff — an optional trailing section, same shape telltale's own
    `_regressions_table` adds, only rendered when there's something to say. `enumeration` is
    empty whenever --enumerate wasn't passed; every server then renders as "not enumerated"
    rather than being dropped — the same "don't conflate absence of evidence with evidence of
    absence" distinction lockfile.py's `tools: null` makes.
    """
    enumeration = enumeration or {}

    lines = [
        "### charter — MCP server capabilities",
        "",
        "| Server | Transport | Tool | Capabilities | Severity |",
        "|---|---|---|---|---|",
    ]
    for server in sorted(servers.servers, key=lambda s: (s.name, str(s.source_file))):
        lines += _tool_rows(server, enumeration.get(server))

    lines += ["", _summary_line(servers, enumeration)]

    if drift:
        lines += [
            "",
            "### Drift since the merge base",
            "",
            "| What | Kind | Before | After |",
            "|---|---|---|---|",
            *_drift_rows(drift),
            "",
            f"**{len(drift)} drift finding(s)** — this check fails on drift, never an absolute "
            "threshold.",
        ]

    return "\n".join(lines) + "\n"
