"""Groups charter's per-server and per-(server, tool, capability) `Finding`s into one
`keel.triage.IssueGroup` per MCP server — a server that cannot be introspected becomes one
`review` group with every unresolved capability folded inside it, rather than reading as
several separate alarms for the same underlying "we don't know" fact.

Consumes `ServerSet`/`enumeration`/`drift` directly, the same live objects `cli.py` already
builds before calling `to_findings` — no re-parsing of rendered `Finding` text.
"""

from pathlib import Path

from keel.finding import ProductCode
from keel.triage import IssueGroup, Lane, OccurrenceRef, TriageResult, make_fingerprint

from charter.capability import CapabilityClass, classify_tool
from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet

_HIGH_RISK_CAPABILITIES = {
    CapabilityClass.CREDENTIAL_ACCESS.value,
    CapabilityClass.CODE_EXECUTION.value,
}
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

    groups: list[IssueGroup] = []
    for server in server_set.servers:
        result = enumeration.get(server)
        occurrences = [_server_occurrence(server, root)]
        capability_values: set[str] = set()

        if result is not None and result.error is None and result.tools:
            tool_occurrences, capability_values = _tool_occurrences(server, result.tools, root)
            occurrences.extend(tool_occurrences)

        is_new = server.name in new_servers
        is_drifted = server.name in drifted_servers

        if is_new:
            lane, reason = Lane.ACT_NOW, "Act now · new server since baseline"
        elif is_drifted:
            lane, reason = Lane.ACT_NOW, "Act now · capability profile changed since baseline"
        elif result is None:
            lane = Lane.REVIEW
            reason = "Review · not enumerated — static parsing cannot say what tools it exposes"
        elif result.error is not None:
            lane = Lane.REVIEW
            reason = f"Review · enumeration failed: {result.error}"
        elif capability_values & _HIGH_RISK_CAPABILITIES:
            risky = ", ".join(sorted(capability_values & _HIGH_RISK_CAPABILITIES))
            lane, reason = Lane.PLAN, f"Plan · exposes {risky} capability"
        else:
            tool_count = len(result.tools) if result is not None else 0
            lane = Lane.INVENTORY
            reason = f"Inventory · enumerated, {tool_count} tool(s), no elevated capability"

        groups.append(
            IssueGroup(
                fingerprint=make_fingerprint("charter", server.name),
                product=ProductCode.CHARTER,
                label=server.name,
                lane=lane,
                reason=reason,
                occurrence_count=len(occurrences),
                context_count=len(capability_values) or 1,
                occurrences=occurrences,
                deadline=None,
                attributes={
                    "transport": server.transport.value,
                    "tool_count": str(len(result.tools)) if result is not None else "-",
                },
            )
        )

    return TriageResult(
        product=ProductCode.CHARTER,
        groups=groups,
        observations=sum(g.occurrence_count for g in groups),
    )
