from pathlib import Path

from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet, Transport
from charter.render.markdown import render_markdown


def make_server(**overrides: object) -> Server:
    defaults: dict[str, object] = {
        "name": "svc",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("-y", "server"),
        "env_var_names": ("API_KEY",),
        "url": None,
        "header_names": (),
        "source_file": Path("/repo/.mcp.json"),
        "source_line": 3,
    }
    defaults.update(overrides)
    return Server(**defaults)  # type: ignore[arg-type]


def test_a_server_not_enumerated_is_marked_as_such() -> None:
    output = render_markdown(ServerSet(servers=(make_server(),)))

    assert "svc" in output
    assert "not enumerated" in output


def test_a_successfully_enumerated_tool_shows_capabilities_and_severity() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="execute_command", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "execute_command" in output
    assert "code_execution" in output
    assert "critical" in output


def test_a_tool_matching_two_capabilities_lists_both() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="web_search", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "network_egress, read" in output


def test_an_unrecognized_tool_shows_unknown() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="frobnicate", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "unknown" in output


def test_a_server_with_zero_tools_says_so() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "no tools" in output


def test_a_failed_enumeration_shows_the_error() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="timed out after 10.0s")

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "timed out after 10.0s" in output


def test_a_pipe_in_a_tool_name_does_not_break_the_table() -> None:
    # Tool names come verbatim from a live, untrusted third-party server's own tools/list
    # response (DEC-02) — a literal "|" would otherwise split into an extra markdown table
    # column, same reasoning render/terminal.py's rich.markup.escape use documents for its own
    # surface.
    server = make_server()
    tool = Tool(server_name="svc", name="evil|tool", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    rows = [line for line in output.splitlines() if "evil" in line]
    assert len(rows) == 1
    assert "evil\\|tool" in rows[0]
    # Split on the field separator, not the bare pipe character, since the escaped "\|" inside
    # the tool name still contains a literal "|" — the row must still have exactly 5 columns
    # (a leading/trailing empty string from the outer "| ... |" wrapping, plus 5 fields).
    assert len(rows[0].split(" | ")) == 5


def test_summary_line_counts_by_severity() -> None:
    server = make_server()
    tools = (
        Tool(server_name="svc", name="execute_command", description=None, input_schema=None),
        Tool(server_name="svc", name="read_file", description=None, input_schema=None),
    )
    result = EnumerationResult(server_name="svc", tools=tools, error=None)

    output = render_markdown(ServerSet(servers=(server,)), {server: result})

    assert "2 server(s)" not in output  # only one server here
    assert "1 server(s), 2 tool(s)" in output
    assert "1 critical" in output
    assert "1 low" in output


def test_empty_server_set_still_renders() -> None:
    output = render_markdown(ServerSet(servers=()))

    assert "0 server(s), 0 tool(s)" in output


def test_no_drift_omits_the_drift_section() -> None:
    output = render_markdown(ServerSet(servers=()))

    assert "Drift since the merge base" not in output


def test_drift_adds_a_trailing_section() -> None:
    drift = (
        Drift(
            server_name="svc",
            source_file=".mcp.json",
            source_line=3,
            tool_name="write_file",
            kind=DriftKind.NEW_TOOL,
            before_severity=None,
            after_severity="high",
        ),
    )

    output = render_markdown(ServerSet(servers=()), drift=drift)

    assert "Drift since the merge base" in output
    assert "1 drift finding(s)" in output
    assert "write_file" in output
    assert "high" in output
