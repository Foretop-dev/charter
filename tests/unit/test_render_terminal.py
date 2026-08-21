from pathlib import Path

from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet, Transport
from charter.render.terminal import render_drift, render_terminal


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


def test_renders_the_server_name_and_transport() -> None:
    output = render_terminal(
        ServerSet(servers=(make_server(),)), Path("/repo/charter.lock"), no_color=True
    )

    assert "svc" in output
    assert "stdio" in output


def test_renders_the_env_var_name_never_a_value() -> None:
    output = render_terminal(
        ServerSet(servers=(make_server(env_var_names=("SECRET_NAME",)),)),
        Path("/repo/charter.lock"),
        no_color=True,
    )

    assert "SECRET_NAME" in output


def test_a_remote_server_shows_its_url() -> None:
    server = make_server(
        transport=Transport.HTTP, command=None, url="https://example.com/mcp", env_var_names=()
    )
    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), no_color=True
    )

    assert "https://example.com/mcp" in output


def test_summary_line_counts_servers() -> None:
    output = render_terminal(
        ServerSet(servers=(make_server(name="a"), make_server(name="b"))),
        Path("/repo/charter.lock"),
        no_color=True,
    )

    assert "2 server(s)" in output


def test_empty_server_set_still_renders_a_zero_summary() -> None:
    output = render_terminal(ServerSet(servers=()), Path("/repo/charter.lock"), no_color=True)

    assert "0 server(s)" in output


def test_summary_line_includes_the_lock_path() -> None:
    output = render_terminal(ServerSet(servers=()), Path("/repo/charter.lock"), no_color=True)

    assert "charter.lock" in output


def test_a_server_not_enumerated_shows_an_em_dash_in_the_tools_column() -> None:
    server = make_server()
    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), {}, no_color=True
    )

    assert "—" in output


def test_a_successfully_enumerated_server_shows_its_tool_name_and_severity() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="read_file", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), {server: result}, no_color=True
    )

    # Not just the bare name — the severity suffix is the actual point of this column. A
    # regression here (an earlier version of this function silently swallowed the "[low]"
    # suffix in the no_color branch, since Rich's Table parses plain cell text as markup and
    # "[low]" isn't a recognized style tag) would still leave "read_file" in the output, so
    # asserting the name alone is not enough to catch it.
    assert "read_file [low]" in output


def test_a_successfully_enumerated_server_shows_severity_in_color_too() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="execute_command", description=None, input_schema=None)
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_terminal(
        ServerSet(servers=(server,)),
        Path("/repo/charter.lock"),
        {server: result},
        no_color=False,
    )

    assert "[critical]" in output


def test_a_tool_name_containing_bracket_syntax_does_not_break_rendering() -> None:
    # Tool name/description come verbatim from a live, untrusted third-party server's own
    # tools/list response (DEC-02) — a real or malicious server could send a name shaped like
    # Rich markup ("[/bold red]"). It must render as literal text, never get parsed as a style
    # tag or silently disappear the way the un-escaped no_color path once did.
    server = make_server()
    tool = Tool(
        server_name="svc", name="evil[/bold red]tool[red]", description=None, input_schema=None
    )
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), {server: result}, no_color=True
    )

    assert "evil[/bold red]tool[red]" in output


def test_a_server_name_containing_bracket_syntax_does_not_break_rendering() -> None:
    server = make_server(name="oops[bold red]")

    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), no_color=True
    )

    assert "oops[bold red]" in output


def test_a_failed_enumeration_shows_the_error() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="timed out after 10.0s")

    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), {server: result}, no_color=True
    )

    assert "timed out after 10.0s" in output


def test_a_failed_enumeration_error_containing_bracket_syntax_does_not_break_rendering() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="server said [bold]boom[/bold]")

    output = render_terminal(
        ServerSet(servers=(server,)), Path("/repo/charter.lock"), {server: result}, no_color=True
    )

    assert "server said [bold]boom[/bold]" in output


def test_render_drift_shows_a_new_server() -> None:
    drift = (
        Drift(
            server_name="evil-svc",
            source_file=".mcp.json",
            source_line=5,
            tool_name=None,
            kind=DriftKind.NEW_SERVER,
            before_severity=None,
            after_severity=None,
        ),
    )

    output = render_drift(drift, no_color=True)

    assert "evil-svc" in output
    assert "new_server" in output
    assert "1 drift finding(s)" in output


def test_render_drift_shows_severity_increase() -> None:
    drift = (
        Drift(
            server_name="svc",
            source_file=".mcp.json",
            source_line=3,
            tool_name="t",
            kind=DriftKind.SEVERITY_INCREASED,
            before_severity="low",
            after_severity="critical",
        ),
    )

    output = render_drift(drift, no_color=True)

    assert "low" in output
    assert "critical" in output


def test_render_drift_escapes_bracket_syntax_in_names() -> None:
    drift = (
        Drift(
            server_name="evil[/bold red]svc",
            source_file=".mcp.json",
            source_line=1,
            tool_name=None,
            kind=DriftKind.NEW_SERVER,
            before_severity=None,
            after_severity=None,
        ),
    )

    output = render_drift(drift, no_color=True)

    assert "evil[/bold red]svc" in output
