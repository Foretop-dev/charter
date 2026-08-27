from pathlib import Path

from keel.triage import Lane

from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet, Transport
from charter.triage import build_triage

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


def test_a_never_enumerated_server_is_one_review_group_not_multiple_alarms() -> None:
    """An HTTP/SSE/WS server is never contacted (enumerate.py's own stdio-only guard) — this
    must land as exactly one group, not one per undeclared capability."""
    server = make_server(transport=Transport.HTTP, command=None, url="https://example.com/mcp")
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {}, (), ROOT)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.lane is Lane.REVIEW
    assert "not enumerated" in group.reason
    assert group.occurrence_count == 1


def test_an_enumeration_error_is_also_review() -> None:
    server = make_server()
    result = EnumerationResult(server_name=server.name, tools=(), error="connection refused")
    server_set = ServerSet(servers=(server,))

    triage = build_triage(server_set, {server: result}, (), ROOT)

    assert triage.groups[0].lane is Lane.REVIEW
    assert "connection refused" in triage.groups[0].reason


def test_a_new_server_since_baseline_is_act_now() -> None:
    server = make_server()
    server_set = ServerSet(servers=(server,))
    drift = (make_drift(server_name=server.name, kind=DriftKind.NEW_SERVER),)

    result = build_triage(server_set, {}, drift, ROOT)

    assert result.groups[0].lane is Lane.ACT_NOW
    assert "new server" in result.groups[0].reason


def test_a_drifted_tool_is_act_now() -> None:
    server = make_server()
    tool = make_tool(name="run_shell", description="Execute a shell command")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))
    drift = (make_drift(server_name=server.name, tool_name=tool.name, kind=DriftKind.NEW_TOOL),)

    result = build_triage(server_set, {server: enum_result}, drift, ROOT)

    assert result.groups[0].lane is Lane.ACT_NOW
    assert "changed since baseline" in result.groups[0].reason


def test_an_enumerated_server_with_a_high_risk_capability_is_plan() -> None:
    server = make_server()
    tool = make_tool(name="run_shell", description="Execute an arbitrary shell command")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    group = result.groups[0]
    assert group.lane is Lane.PLAN
    assert "code_execution" in group.reason


def test_a_clean_enumerated_server_is_inventory() -> None:
    server = make_server()
    tool = make_tool(name="read_file", description="Read a file from disk")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    assert result.groups[0].lane is Lane.INVENTORY


def test_multiple_tools_and_capabilities_all_fold_into_one_group() -> None:
    server = make_server()
    tools = (
        make_tool(name="read_file", description="Read a file from disk"),
        make_tool(name="write_file", description="Write or delete a file on disk"),
        make_tool(name="run_shell", description="Execute an arbitrary shell command"),
    )
    enum_result = EnumerationResult(server_name=server.name, tools=tools, error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    assert len(result.groups) == 1
    group = result.groups[0]
    # 1 server-presence occurrence + 3 tools x >=1 capability each.
    assert group.occurrence_count >= 4
    assert group.context_count >= 3


def test_observations_sums_every_occurrence_across_all_groups() -> None:
    server_a = make_server(name="svc-a")
    server_b = make_server(name="svc-b", source_file=ROOT / ".cursor" / "mcp.json")
    server_set = ServerSet(servers=(server_a, server_b))

    result = build_triage(server_set, {}, (), ROOT)

    assert result.issue_groups == 2
    assert result.observations == 2  # one server-presence occurrence each, neither enumerated
