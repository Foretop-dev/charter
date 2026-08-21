import json
import sys
import time
from pathlib import Path

from charter.enumerate import _expand_vars, enumerate_stdio_server
from charter.models import Server, Transport

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "toy_mcp_server.py").resolve()


def make_server(tmp_path: Path, *extra_args: str, env_var_names: tuple[str, ...] = ()) -> Server:
    config = {
        "mcpServers": {
            "toy": {
                "command": sys.executable,
                "args": [str(FIXTURE), *extra_args],
                "env": dict.fromkeys(env_var_names, "should-never-leak-anywhere"),
            }
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(config))
    return Server(
        name="toy",
        transport=Transport.STDIO,
        command=sys.executable,
        args=(str(FIXTURE), *extra_args),
        env_var_names=env_var_names,
        url=None,
        header_names=(),
        source_file=tmp_path / ".mcp.json",
        source_line=3,
    )


# These tests launch a real subprocess (tests/fixtures/toy_mcp_server.py) and speak real
# newline-delimited JSON-RPC to it — not a mock. That fixture was hand-written from the same
# MCP spec pages charter/enumerate.py itself was built against, so a passing test here is
# evidence the wire protocol handling actually works, not just that two halves of a mock agree
# with each other.


def test_a_real_server_returns_its_real_tools(tmp_path: Path) -> None:
    server = make_server(tmp_path, env_var_names=("FAKE_SECRET",))

    result = enumerate_stdio_server(server, timeout=5.0)

    assert result.error is None
    assert {t.name for t in result.tools} == {"read_file", "write_file"}
    read_tool = next(t for t in result.tools if t.name == "read_file")
    assert read_tool.description == "Read a file from disk"
    assert read_tool.input_schema is not None
    assert read_tool.input_schema["required"] == ["path"]
    assert read_tool.server_name == "toy"


def test_env_values_never_appear_anywhere_in_the_result(tmp_path: Path) -> None:
    server = make_server(tmp_path, env_var_names=("FAKE_SECRET",))

    result = enumerate_stdio_server(server, timeout=5.0)

    assert "should-never-leak-anywhere" not in repr(result)
    assert all("should-never-leak-anywhere" not in repr(t) for t in result.tools)


def test_a_tools_list_protocol_error_is_reported_not_raised(tmp_path: Path) -> None:
    server = make_server(tmp_path, "--fail-tools-list")

    result = enumerate_stdio_server(server, timeout=5.0)

    assert result.tools == ()
    assert result.error is not None
    assert "tools/list failed" in result.error


def test_a_server_that_crashes_before_responding_is_reported_not_raised(tmp_path: Path) -> None:
    server = make_server(tmp_path, "--crash-on-init")

    result = enumerate_stdio_server(server, timeout=5.0)

    assert result.tools == ()
    assert result.error is not None


def test_a_hung_server_times_out_and_is_killed_not_left_running(tmp_path: Path) -> None:
    server = make_server(tmp_path, "--sleep-forever")

    start = time.monotonic()
    result = enumerate_stdio_server(server, timeout=1.0)
    elapsed = time.monotonic() - start

    assert result.error is not None
    assert "timed out" in result.error
    # 1s timeout + up to 2s shutdown grace + 2s SIGTERM grace, generously bounded — proves the
    # subprocess was actually killed rather than this test hanging until the suite's own
    # timeout (or forever, in a plain `pytest` run with no timeout plugin at all).
    assert elapsed < 10.0


def test_a_nonexistent_command_fails_to_launch_without_raising(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"broken": {"command": "definitely-not-a-real-binary-xyz"}}}'
    )
    server = Server(
        name="broken",
        transport=Transport.STDIO,
        command="definitely-not-a-real-binary-xyz",
        args=(),
        env_var_names=(),
        url=None,
        header_names=(),
        source_file=tmp_path / ".mcp.json",
        source_line=1,
    )

    result = enumerate_stdio_server(server, timeout=5.0)

    assert result.tools == ()
    assert result.error is not None
    assert "failed to launch" in result.error


def test_a_remote_server_is_rejected_without_attempting_anything(tmp_path: Path) -> None:
    server = Server(
        name="remote",
        transport=Transport.HTTP,
        command=None,
        args=(),
        env_var_names=(),
        url="https://example.com/mcp",
        header_names=(),
        source_file=tmp_path / ".mcp.json",
        source_line=1,
    )

    result = enumerate_stdio_server(server)

    assert result.tools == ()
    assert result.error is not None
    assert "stdio" in result.error.lower()


class TestExpandVars:
    def test_expands_a_known_variable(self) -> None:
        assert _expand_vars("${API_KEY}", {"API_KEY": "real-value"}) == "real-value"

    def test_uses_the_default_when_missing(self) -> None:
        assert _expand_vars("${MISSING:-fallback}", {}) == "fallback"

    def test_prefers_the_real_value_over_a_default(self) -> None:
        assert _expand_vars("${KEY:-fallback}", {"KEY": "real"}) == "real"

    def test_a_missing_variable_with_no_default_is_left_literal(self) -> None:
        # Matches Claude Code's own documented degrade behavior exactly (fetched live) — not
        # charter's own invention.
        assert _expand_vars("${MISSING}", {}) == "${MISSING}"

    def test_expands_inside_a_larger_string(self) -> None:
        result = _expand_vars("prefix-${KEY}-suffix", {"KEY": "mid"})
        assert result == "prefix-mid-suffix"

    def test_a_url_shaped_default_expands_correctly(self) -> None:
        result = _expand_vars("${BASE:-https://api.example.com}/mcp", {})
        assert result == "https://api.example.com/mcp"

    def test_multiple_variables_in_one_string(self) -> None:
        result = _expand_vars("${A}/${B}", {"A": "x", "B": "y"})
        assert result == "x/y"

    def test_no_variables_is_a_no_op(self) -> None:
        assert _expand_vars("plain string", {}) == "plain string"
