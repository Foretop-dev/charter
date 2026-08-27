import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from keel.finding import (
    Confidence,
    Evidence,
    Finding,
    FindingState,
    ProductCode,
    Verdict,
)
from keel.finding import Severity as KeelSeverity
from keel.owner import owners_from_codeowners

from charter import __version__ as ENGINE_VERSION  # noqa: N812
from charter.capability import CapabilityClass, classify_tool, severity_for_capability
from charter.capability import Severity as CharterSeverity
from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult
from charter.models import Server, ServerSet

RULE_ID_SERVER = "charter.server"
RULE_ID_CAPABILITY = "charter.capability"
_UNKNOWN_CAPABILITY = "unknown"

# charter.capability.Severity (LOW/MEDIUM/HIGH/CRITICAL, no INFO tier) is a real, separate
# StrEnum from keel.finding.Severity (INFO/LOW/MEDIUM/HIGH/CRITICAL) — the string *values*
# happen to match, but they're different classes and Finding.severity is typed against keel's.
# A 1:1 mapping, not a truncation: charter never produces INFO today.
_TO_KEEL_SEVERITY: dict[CharterSeverity, KeelSeverity] = {
    CharterSeverity.LOW: KeelSeverity.LOW,
    CharterSeverity.MEDIUM: KeelSeverity.MEDIUM,
    CharterSeverity.HIGH: KeelSeverity.HIGH,
    CharterSeverity.CRITICAL: KeelSeverity.CRITICAL,
}


def compute_identity(server_name: str, tool_name: str | None, capability: str | None) -> str:
    """SUITE_ARCHITECTURE.md §3.1: "server name + tool name + capability class" for charter.
    `tool_name`/`capability` are both None for a server-presence finding (the whole server is
    the subject, not one tool) — the same shape ebb's own compute_identity and telltale's own
    use, hashed rather than displayed since identity is compared, never shown."""
    raw = f"charter|{server_name}|{tool_name or '-'}|{capability or '-'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _content_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _owner_for(path: Path, root: Path) -> str | None:
    """H7: CODEOWNERS-only, no git-blame fallback — a server's evidence anchors on its own
    config location (a file, e.g. `.mcp.json`), not a line, so there is nothing for git-blame
    to attribute against."""
    owners = owners_from_codeowners(path, root)
    return owners[0] if owners else None


def _server_evidence(server: Server, root: Path, retrieved_at: datetime) -> list[Evidence]:
    reach = server.command or server.url or server.name
    return [
        Evidence(
            source_uri=_relative(server.source_file, root),
            locator=str(server.source_line),
            excerpt=f"{server.name} ({server.transport.value}): {reach}",
            retrieved_at=retrieved_at,
            content_hash=_content_hash(server.source_file),
        )
    ]


def _new_server_names(drift: tuple[Drift, ...]) -> set[str]:
    return {d.server_name for d in drift if d.kind is DriftKind.NEW_SERVER}


def _drifted_tools(drift: tuple[Drift, ...]) -> set[tuple[str, str]]:
    return {
        (d.server_name, d.tool_name)
        for d in drift
        if d.kind in (DriftKind.NEW_TOOL, DriftKind.SEVERITY_INCREASED) and d.tool_name
    }


def _server_presence_finding(
    server: Server,
    result: EnumerationResult | None,
    *,
    is_new: bool,
    root: Path,
    run_id: str,
    retrieved_at: datetime,
) -> Finding:
    """One per server, always. Deliberately diverges from render/sarif.py, which emits nothing
    for a non-enumerated server ("no evidence was ever gathered") — the right call for a
    snapshot format, but the Finding schema exists specifically so unknown is never silent
    (SUITE_ARCHITECTURE.md §3: "unknown is a verdict, not the absence of one"). An enumeration
    error lands here too (collector failure = unknown), rather than inventing a third finding
    kind for it.
    """
    if is_new:
        verdict, severity = Verdict.BREAK, KeelSeverity.HIGH
        title = f"{server.name} is a new server"
        detail = (
            f"`{server.name}` ({server.transport.value}) was not present at the merge base. "
            "New servers are exactly what this check exists to surface (DEC-04)."
        )
    elif result is None:
        verdict, severity = Verdict.UNKNOWN, KeelSeverity.MEDIUM
        title = f"{server.name} capabilities are unknown (not enumerated)"
        detail = (
            f"`{server.name}` was declared but never `--enumerate`'d — static parsing alone "
            "cannot say what tools it exposes (DEC-02)."
        )
    elif result.error is not None:
        verdict, severity = Verdict.UNKNOWN, KeelSeverity.MEDIUM
        title = f"{server.name} enumeration failed"
        detail = f"Enumeration was attempted and failed: `{result.error}`."
    else:
        verdict, severity = Verdict.CLEAR, KeelSeverity.LOW
        count = len(result.tools)
        title = f"{server.name} enumerated, {count} tool(s)"
        detail = f"`{server.name}` responded to `tools/list` with {count} tool(s)."

    return Finding(
        identity=compute_identity(server.name, None, None),
        product=ProductCode.CHARTER,
        rule_id=RULE_ID_SERVER,
        subject=server.name,
        severity=severity,
        confidence=Confidence.CERTAIN if result is not None else Confidence.PROBABLE,
        title=title,
        detail_md=detail,
        evidence=_server_evidence(server, root, retrieved_at),
        owner=_owner_for(server.source_file, root),
        deadline=None,
        verdict=verdict,
        state=FindingState.NEW,
        first_seen_run=run_id,
        rule_version=ENGINE_VERSION,
        engine_version=ENGINE_VERSION,
    )


_CAPABILITY_DETAIL: dict[str, str] = {
    CapabilityClass.READ.value: "can read or retrieve information.",
    CapabilityClass.WRITE.value: "can create, modify, or delete data.",
    CapabilityClass.NETWORK_EGRESS.value: "can make outbound network requests.",
    CapabilityClass.CODE_EXECUTION.value: "can execute code or shell commands.",
    CapabilityClass.CREDENTIAL_ACCESS.value: "can access credentials or secrets.",
    _UNKNOWN_CAPABILITY: (
        "did not match any known capability pattern (DEC-03: a real, honest verdict, "
        "not guessed at)."
    ),
}


def _tool_capability_findings(
    server: Server,
    result: EnumerationResult,
    drifted_tools: set[tuple[str, str]],
    *,
    root: Path,
    rule_version: int,
    run_id: str,
    retrieved_at: datetime,
) -> list[Finding]:
    """One finding per (server, tool, matched capability) — the exact granularity
    render/sarif.py already established ("a tool matching more than one capability produces
    multiple results rather than one merged one"). Evidence is the tool's *server* location:
    an enumerated tool has no line of its own, it comes from a live tools/list call, not
    static text — same anchor SARIF already uses for the identical problem.
    """
    findings = []
    evidence = _server_evidence(server, root, retrieved_at)
    owner = _owner_for(server.source_file, root)

    for tool in result.tools:
        classification = classify_tool(tool)
        is_drifted = (server.name, tool.name) in drifted_tools
        capabilities: list[CapabilityClass | None] = (
            list(classification.capabilities) if classification.capabilities else [None]
        )

        for capability in capabilities:
            cap_value = capability.value if capability is not None else _UNKNOWN_CAPABILITY
            known = capability is not None

            if is_drifted:
                verdict, severity = Verdict.BREAK, KeelSeverity.HIGH
                title = f"{server.name}/{tool.name} capability profile changed since base"
                detail = (
                    f"`{tool.name}` on `{server.name}` gained a tool or its severity "
                    "increased since the merge base (DEC-04) — Drift does not isolate which "
                    "specific capability moved, so every capability finding for this tool is "
                    "flagged; a real, stated imprecision, not hidden."
                )
            elif capability is not None:
                verdict = Verdict.CLEAR
                severity = _TO_KEEL_SEVERITY[severity_for_capability(capability)]
                title = f"{server.name}/{tool.name} {_CAPABILITY_DETAIL[cap_value]}"
                detail = f"`{tool.name}` on `{server.name}` {_CAPABILITY_DETAIL[cap_value]}"
            else:
                verdict, severity = Verdict.UNKNOWN, KeelSeverity.MEDIUM
                title = f"{server.name}/{tool.name} capability could not be determined"
                detail = f"`{tool.name}` on `{server.name}` {_CAPABILITY_DETAIL[cap_value]}"

            findings.append(
                Finding(
                    identity=compute_identity(server.name, tool.name, cap_value),
                    product=ProductCode.CHARTER,
                    rule_id=RULE_ID_CAPABILITY,
                    subject=f"{server.name}/{tool.name}",
                    severity=severity,
                    confidence=Confidence.CERTAIN if known else Confidence.AMBIGUOUS,
                    title=title,
                    detail_md=detail,
                    evidence=evidence,
                    owner=owner,
                    deadline=None,
                    verdict=verdict,
                    state=FindingState.NEW,
                    first_seen_run=run_id,
                    rule_version=str(rule_version),
                    engine_version=ENGINE_VERSION,
                )
            )
    return findings


def to_findings(
    server_set: ServerSet,
    enumeration: dict[Server, EnumerationResult],
    drift: tuple[Drift, ...],
    root: Path,
    *,
    run_id: str | None = None,
    retrieved_at: datetime | None = None,
) -> list[Finding]:
    """Maps charter's own ServerSet/EnumerationResult/Drift onto the suite-wide Finding schema
    (keel.finding). charter's own domain model is unchanged; this is an adapter, not a
    replacement — same pattern telltale/findings.py already established in Session 26.

    `deadline` is always None (SUITE_ARCHITECTURE.md §3: charter has none). `owner` resolves via
    CODEOWNERS against the server's own config location (H7, `keel.owner`) — None when no rule
    matches. `state` is always NEW — no baseline mechanism in the stateless CLI yet.
    """
    run_id = run_id or f"run-{uuid.uuid4()}"
    retrieved_at = retrieved_at or datetime.now(UTC)
    new_servers = _new_server_names(drift)
    drifted_tools = _drifted_tools(drift)

    findings: list[Finding] = []
    for server in server_set.servers:
        result = enumeration.get(server)
        findings.append(
            _server_presence_finding(
                server,
                result,
                is_new=server.name in new_servers,
                root=root,
                run_id=run_id,
                retrieved_at=retrieved_at,
            )
        )
        if result is not None and result.error is None and result.tools:
            # rule_version is real only once at least one tool was actually classified —
            # classify_tool loads the taxonomy lazily, so this stays honest for a server with
            # no tools rather than reporting a version nothing was checked against.
            rule_version = classify_tool(result.tools[0]).rule_version
            findings.extend(
                _tool_capability_findings(
                    server,
                    result,
                    drifted_tools,
                    root=root,
                    rule_version=rule_version,
                    run_id=run_id,
                    retrieved_at=retrieved_at,
                )
            )
    return findings
