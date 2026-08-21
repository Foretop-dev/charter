import json
from pathlib import Path

from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet, Transport
from charter.render.sarif import render_sarif


def make_server(**overrides: object) -> Server:
    defaults: dict[str, object] = {
        "name": "svc",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("-y", "server"),
        "env_var_names": (),
        "url": None,
        "header_names": (),
        "source_file": Path("/repo/.mcp.json"),
        "source_line": 3,
    }
    defaults.update(overrides)
    return Server(**defaults)  # type: ignore[arg-type]


def test_output_is_json_serializable() -> None:
    log = render_sarif(ServerSet(servers=()), {})
    json.dumps(log)  # must not raise


def test_top_level_shape() -> None:
    log = render_sarif(ServerSet(servers=()), {})

    assert log["version"] == "2.1.0"
    assert "$schema" in log
    assert len(log["runs"]) == 1  # type: ignore[arg-type]


def test_rule_catalog_has_all_five_capabilities_plus_unknown() -> None:
    log = render_sarif(ServerSet(servers=()), {})

    rule_ids = {r["id"] for r in log["runs"][0]["tool"]["driver"]["rules"]}  # type: ignore[index]
    assert rule_ids == {
        "read",
        "write",
        "network_egress",
        "code_execution",
        "credential_access",
        "unknown",
    }


def test_an_unenumerated_server_produces_no_results() -> None:
    log = render_sarif(ServerSet(servers=(make_server(),)), {})

    assert log["runs"][0]["results"] == []  # type: ignore[index]


def test_a_failed_enumeration_produces_no_results() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="timed out")

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    assert log["runs"][0]["results"] == []  # type: ignore[index]


def test_a_classified_tool_produces_a_result_with_the_right_ruleid_and_level() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="execute_command", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    results = log["runs"][0]["results"]  # type: ignore[index]
    assert len(results) == 1
    assert results[0]["ruleId"] == "code_execution"
    assert results[0]["level"] == "error"
    assert "execute_command" in results[0]["message"]["text"]


def test_an_unrecognized_tool_gets_the_unknown_ruleid() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="frobnicate", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    results = log["runs"][0]["results"]  # type: ignore[index]
    assert len(results) == 1
    assert results[0]["ruleId"] == "unknown"
    assert results[0]["level"] == "warning"  # MEDIUM -> warning


def test_a_tool_matching_two_capabilities_produces_two_results() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="web_search", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    results = log["runs"][0]["results"]  # type: ignore[index]
    rule_ids = {r["ruleId"] for r in results}
    assert rule_ids == {"read", "network_egress"}


def test_result_location_points_at_the_servers_config_source() -> None:
    server = make_server(source_file=Path("/repo/.cursor/mcp.json"), source_line=7)
    tool = Tool(server_name="svc", name="read_file", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    location = log["runs"][0]["results"][0]["locations"][0]  # type: ignore[index]
    assert location["physicalLocation"]["artifactLocation"]["uri"] == "/repo/.cursor/mcp.json"
    assert location["physicalLocation"]["region"]["startLine"] == 7


def test_severity_to_level_mapping_covers_every_severity() -> None:
    server = make_server()
    # One tool per severity tier: read=LOW, network_egress=MEDIUM, write=HIGH,
    # code_execution=CRITICAL (charter.capability's own _CAPABILITY_SEVERITY table).
    tools = (
        Tool(server_name="svc", name="read_file", description=None, input_schema=None),
        Tool(server_name="svc", name="fetch", description=None, input_schema=None),
        Tool(server_name="svc", name="write_file", description=None, input_schema=None),
        Tool(server_name="svc", name="execute_command", description=None, input_schema=None),
    )
    result = EnumerationResult(server_name="svc", tools=tools, error=None)

    log = render_sarif(ServerSet(servers=(server,)), {server: result})

    levels_by_rule = {r["ruleId"]: r["level"] for r in log["runs"][0]["results"]}  # type: ignore[index]
    assert levels_by_rule["read"] == "note"
    assert levels_by_rule["network_egress"] == "warning"
    assert levels_by_rule["write"] == "error"
    assert levels_by_rule["code_execution"] == "error"
