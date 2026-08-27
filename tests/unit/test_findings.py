from pathlib import Path

from keel.finding import Confidence, ProductCode, Verdict
from keel.finding import Severity as KeelSeverity

from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.findings import compute_identity, to_findings
from charter.models import Server, ServerSet, Transport

ROOT = Path("/repo")


def make_server(**overrides: object) -> Server:
    defaults: dict[str, object] = {
        "name": "svc",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("-y", "server"),
        "env_var_names": ("API_KEY",),
        "url": None,
        "header_names": (),
        "source_file": ROOT / ".mcp.json",
        "source_line": 3,
    }
    defaults.update(overrides)
    return Server(**defaults)  # type: ignore[arg-type]


def make_tool(**overrides: object) -> Tool:
    defaults: dict[str, object] = {
        "server_name": "svc",
        "name": "read_file",
        "description": "Read a file from disk",
        "input_schema": None,
    }
    defaults.update(overrides)
    return Tool(**defaults)  # type: ignore[arg-type]


def make_drift(
    server_name: str = "svc",
    tool_name: str | None = None,
    kind: DriftKind = DriftKind.NEW_SERVER,
) -> Drift:
    return Drift(
        server_name=server_name,
        source_file=".mcp.json",
        source_line=3,
        tool_name=tool_name,
        kind=kind,
        before_severity=None,
        after_severity=None,
    )


# --- identity: the property SUITE_ARCHITECTURE.md §3.1 demands -----------------------------


def test_server_presence_identity_survives_the_server_moving_in_the_config() -> None:
    before = to_findings(ServerSet(servers=(make_server(source_line=3),)), {}, (), ROOT)
    after = to_findings(ServerSet(servers=(make_server(source_line=99),)), {}, (), ROOT)

    assert before[0].identity == after[0].identity


def test_identity_separates_server_tool_and_capability() -> None:
    base = compute_identity("svc", None, None)

    assert base != compute_identity("other", None, None)
    assert base != compute_identity("svc", "read_file", None)
    assert compute_identity("svc", "read_file", "read") != compute_identity(
        "svc", "read_file", "write"
    )


# --- server-presence finding --------------------------------------------------------------


def test_a_non_enumerated_server_is_unknown() -> None:
    findings = to_findings(ServerSet(servers=(make_server(),)), {}, (), ROOT)

    assert len(findings) == 1
    f = findings[0]
    assert f.verdict is Verdict.UNKNOWN
    assert f.product is ProductCode.CHARTER
    assert f.subject == "svc"
    assert f.evidence[0].source_uri == ".mcp.json"
    assert f.evidence[0].locator == "3"
    assert f.deadline is None
    assert f.owner is None  # no CODEOWNERS file at this fixture root


def test_an_enumerated_server_with_zero_tools_is_clear() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error=None)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), ROOT)

    assert len(findings) == 1
    assert findings[0].verdict is Verdict.CLEAR


def test_an_enumeration_error_is_unknown_not_clear() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="connection refused")

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), ROOT)

    assert len(findings) == 1
    assert findings[0].verdict is Verdict.UNKNOWN
    assert "connection refused" in findings[0].detail_md


def test_a_new_server_is_break_even_if_never_enumerated() -> None:
    server = make_server()
    drift = (make_drift(server_name="svc", kind=DriftKind.NEW_SERVER),)

    findings = to_findings(ServerSet(servers=(server,)), {}, drift, ROOT)

    assert findings[0].verdict is Verdict.BREAK
    assert findings[0].severity is KeelSeverity.HIGH


# --- tool-capability findings ---------------------------------------------------------------


def test_a_tool_matching_two_capabilities_yields_two_findings() -> None:
    server = make_server()
    tool = make_tool(
        name="run_query",
        description="Execute a database write query and log the credentials used",
    )
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), ROOT)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert len(capability_findings) >= 2
    severities = {f.severity for f in capability_findings}
    assert KeelSeverity.CRITICAL in severities or KeelSeverity.HIGH in severities


def test_an_unclassified_tool_yields_exactly_one_unknown_finding() -> None:
    server = make_server()
    tool = make_tool(name="mystery_tool", description=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), ROOT)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert len(capability_findings) == 1
    assert capability_findings[0].verdict is Verdict.UNKNOWN
    assert capability_findings[0].confidence is Confidence.AMBIGUOUS


def test_every_capability_finding_for_a_drifted_tool_is_break() -> None:
    server = make_server()
    tool = make_tool(
        name="run_query",
        description="Execute a database write query and log the credentials used",
    )
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)
    drift = (make_drift(server_name="svc", tool_name="run_query", kind=DriftKind.NEW_TOOL),)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, drift, ROOT)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert len(capability_findings) >= 2
    assert all(f.verdict is Verdict.BREAK for f in capability_findings)


def test_a_drift_for_a_different_tool_does_not_mark_this_one() -> None:
    server = make_server()
    tool = make_tool(name="read_file", description="Read a file from disk")
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)
    drift = (make_drift(server_name="svc", tool_name="other_tool", kind=DriftKind.NEW_TOOL),)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, drift, ROOT)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert all(f.verdict is Verdict.CLEAR for f in capability_findings)


def test_tool_capability_evidence_anchors_at_the_server_config() -> None:
    """An enumerated tool has no line of its own — it comes from a live tools/list call."""
    server = make_server(source_file=ROOT / ".mcp.json", source_line=7)
    tool = make_tool()
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), ROOT)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert capability_findings[0].evidence[0].source_uri == ".mcp.json"
    assert capability_findings[0].evidence[0].locator == "7"


# --- schema conformance ---------------------------------------------------------------------


def test_one_run_id_is_shared_across_every_finding_from_one_call() -> None:
    servers = (make_server(name="a"), make_server(name="b"))

    findings = to_findings(ServerSet(servers=servers), {}, (), ROOT)

    assert len({f.first_seen_run for f in findings}) == 1


def test_an_empty_server_set_yields_no_findings() -> None:
    assert to_findings(ServerSet(servers=()), {}, (), ROOT) == []


# --- owner resolution (H7) -------------------------------------------------------------------


def test_owner_resolves_via_codeowners_at_the_servers_config_file(tmp_path: Path) -> None:
    """H7: charter gets CODEOWNERS attribution the same way ebb already does, anchored on the
    server's own config location — a real repo, a real rule, a real match."""
    (tmp_path / "CODEOWNERS").write_text(".mcp.json @platform-team\n")
    server = make_server(source_file=tmp_path / ".mcp.json")

    findings = to_findings(ServerSet(servers=(server,)), {}, (), tmp_path)

    assert findings[0].owner == "@platform-team"


def test_owner_also_applies_to_tool_capability_findings(tmp_path: Path) -> None:
    (tmp_path / "CODEOWNERS").write_text(".mcp.json @platform-team\n")
    server = make_server(source_file=tmp_path / ".mcp.json")
    tool = make_tool()
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    findings = to_findings(ServerSet(servers=(server,)), {server: result}, (), tmp_path)

    capability_findings = [f for f in findings if f.rule_id == "charter.capability"]
    assert capability_findings[0].owner == "@platform-team"
