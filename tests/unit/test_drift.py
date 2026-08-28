from typing import Any

from charter.drift import DriftKind, compute_drift


def make_tool(name: str = "read_file", severity: str = "low") -> dict[str, Any]:
    return {
        "name": name,
        "description": None,
        "input_schema": None,
        "capabilities": [],
        "severity": severity,
        "rule_version": 1,
    }


def make_server(
    name: str = "svc",
    source_file: str = ".mcp.json",
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "transport": "stdio",
        "command": "npx",
        "arg_count": 0,
        "env_var_names": [],
        "url": None,
        "header_names": [],
        "source_file": source_file,
        "source_line": 3,
        "tools": tools,
        "enumeration_error": None,
    }


def make_manifest(servers: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 4, "servers": servers}


def test_no_baseline_means_no_drift() -> None:
    after = make_manifest([make_server(tools=[make_tool()])])

    assert compute_drift(None, after) == ()


def test_a_new_server_is_drift() -> None:
    before = make_manifest([])
    after = make_manifest([make_server(name="new-svc")])

    drifts = compute_drift(before, after)

    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.NEW_SERVER
    assert drifts[0].server_name == "new-svc"
    assert drifts[0].tool_name is None


def test_a_new_server_with_tools_produces_one_drift_not_one_per_tool() -> None:
    before = make_manifest([])
    after = make_manifest(
        [make_server(name="new-svc", tools=[make_tool("a"), make_tool("b", "critical")])]
    )

    drifts = compute_drift(before, after)

    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.NEW_SERVER


def test_an_unchanged_server_and_tool_is_not_drift() -> None:
    before = make_manifest([make_server(tools=[make_tool()])])
    after = make_manifest([make_server(tools=[make_tool()])])

    assert compute_drift(before, after) == ()


def test_a_new_tool_on_an_existing_server_is_drift() -> None:
    before = make_manifest([make_server(tools=[make_tool("read_file")])])
    after = make_manifest(
        [make_server(tools=[make_tool("read_file"), make_tool("write_file", "high")])]
    )

    drifts = compute_drift(before, after)

    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.NEW_TOOL
    assert drifts[0].tool_name == "write_file"
    assert drifts[0].before_severity is None
    assert drifts[0].after_severity == "high"


def test_a_severity_increase_is_drift() -> None:
    before = make_manifest([make_server(tools=[make_tool("t", "low")])])
    after = make_manifest([make_server(tools=[make_tool("t", "critical")])])

    drifts = compute_drift(before, after)

    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.SEVERITY_INCREASED
    assert drifts[0].before_severity == "low"
    assert drifts[0].after_severity == "critical"


def test_a_severity_decrease_is_not_drift() -> None:
    before = make_manifest([make_server(tools=[make_tool("t", "critical")])])
    after = make_manifest([make_server(tools=[make_tool("t", "low")])])

    assert compute_drift(before, after) == ()


def test_a_removed_tool_produces_no_drift() -> None:
    before = make_manifest([make_server(tools=[make_tool("a"), make_tool("b")])])
    after = make_manifest([make_server(tools=[make_tool("a")])])

    assert compute_drift(before, after) == ()


def test_a_removed_server_produces_no_drift() -> None:
    before = make_manifest([make_server(name="gone")])
    after = make_manifest([])

    assert compute_drift(before, after) == ()


def test_tool_level_drift_is_skipped_when_before_was_never_enumerated() -> None:
    # before.tools is None (not attempted) — the server existed, but there's no honest basis
    # to claim write_file is "new": maybe it was always there and just never checked.
    before = make_manifest([make_server(tools=None)])
    after = make_manifest([make_server(tools=[make_tool("write_file", "high")])])

    assert compute_drift(before, after) == ()


def test_tool_level_drift_is_skipped_when_after_was_not_enumerated_this_run() -> None:
    before = make_manifest([make_server(tools=[make_tool("read_file")])])
    after = make_manifest([make_server(tools=None)])

    assert compute_drift(before, after) == ()


def test_servers_are_matched_by_source_file_and_name_together() -> None:
    # Same server name in two different config files is two different servers, not a rename.
    before = make_manifest([make_server(name="svc", source_file=".mcp.json")])
    after = make_manifest([make_server(name="svc", source_file=".cursor/mcp.json")])

    drifts = compute_drift(before, after)

    assert len(drifts) == 1
    assert drifts[0].kind == DriftKind.NEW_SERVER
    assert drifts[0].source_file == ".cursor/mcp.json"


def test_drift_carries_the_servers_config_location() -> None:
    before = make_manifest([])
    after = make_manifest([make_server(name="svc", source_file=".mcp.json")])

    drifts = compute_drift(before, after)

    assert drifts[0].source_file == ".mcp.json"
    assert drifts[0].source_line == 3
