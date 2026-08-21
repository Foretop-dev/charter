from pathlib import Path

from charter.models import Transport
from charter.parsers.claude_code import parse_claude_code_config


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".mcp.json"
    path.write_text(content)
    return path


def test_parses_a_stdio_server(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "airtable": {
      "command": "npx",
      "args": ["-y", "airtable-mcp-server"],
      "env": {"AIRTABLE_API_KEY": "pat_secret_value_should_never_appear"}
    }
  }
}
""",
    )

    servers = parse_claude_code_config(path)

    assert len(servers) == 1
    server = servers[0]
    assert server.name == "airtable"
    assert server.transport == Transport.STDIO
    assert server.command == "npx"
    assert server.args == ("-y", "airtable-mcp-server")
    assert server.env_var_names == ("AIRTABLE_API_KEY",)
    assert server.url is None
    assert server.header_names == ()


def test_env_values_are_never_captured_anywhere_on_the_server(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "svc": {"command": "x", "env": {"SECRET_KEY": "pat_super_secret_value_12345"}}
  }
}
""",
    )

    server = parse_claude_code_config(path)[0]

    assert "pat_super_secret_value_12345" not in repr(server)
    assert "pat_super_secret_value_12345" not in str(server)


def test_parses_a_remote_http_server(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "notion": {
      "type": "http",
      "url": "https://mcp.notion.com/mcp",
      "headers": {"Authorization": "Bearer secret-token-value"}
    }
  }
}
""",
    )

    server = parse_claude_code_config(path)[0]

    assert server.transport == Transport.HTTP
    assert server.url == "https://mcp.notion.com/mcp"
    assert server.header_names == ("Authorization",)
    assert server.command is None


def test_header_values_are_never_captured(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "svc": {"type": "http", "url": "https://x", "headers": {"X-Key": "secret-header-value"}}
  }
}
""",
    )

    server = parse_claude_code_config(path)[0]

    assert "secret-header-value" not in repr(server)


def test_streamable_http_is_an_alias_for_http(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '{"mcpServers": {"svc": {"type": "streamable-http", "url": "https://x"}}}',
    )

    assert parse_claude_code_config(path)[0].transport == Transport.HTTP


def test_sse_and_ws_types_are_recognized(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "sse-svc": {"type": "sse", "url": "https://a"},
    "ws-svc": {"type": "ws", "url": "wss://b"}
  }
}
""",
    )

    servers = {s.name: s.transport for s in parse_claude_code_config(path)}

    assert servers == {"sse-svc": Transport.SSE, "ws-svc": Transport.WS}


def test_a_url_with_no_type_is_a_configuration_error_and_is_skipped(tmp_path: Path) -> None:
    # Claude Code's own real behavior: a `url` with no `type` is skipped and reported as a
    # misconfiguration, never silently guessed at as any particular transport.
    path = write(tmp_path, '{"mcpServers": {"broken": {"url": "https://x"}}}')

    assert parse_claude_code_config(path) == ()


def test_an_unrecognized_type_value_is_skipped(tmp_path: Path) -> None:
    path = write(
        tmp_path, '{"mcpServers": {"broken": {"type": "carrier-pigeon", "url": "https://x"}}}'
    )

    assert parse_claude_code_config(path) == ()


def test_a_stdio_entry_with_no_command_is_skipped(tmp_path: Path) -> None:
    path = write(tmp_path, '{"mcpServers": {"broken": {"args": ["-y", "foo"]}}}')

    assert parse_claude_code_config(path) == ()


def test_multiple_servers_are_all_parsed(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "a": {"command": "x"},
    "b": {"type": "http", "url": "https://y"}
  }
}
""",
    )

    assert {s.name for s in parse_claude_code_config(path)} == {"a", "b"}


def test_missing_mcp_servers_key_yields_no_servers(tmp_path: Path) -> None:
    path = write(tmp_path, '{"title": "empty"}')

    assert parse_claude_code_config(path) == ()


def test_invalid_json_degrades_to_no_servers_not_a_crash(tmp_path: Path) -> None:
    path = write(tmp_path, "{ not valid json [")

    assert parse_claude_code_config(path) == ()


def test_evidence_line_points_at_the_server_entry(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '{\n  "mcpServers": {\n    "svc": {\n      "command": "x"\n    }\n  }\n}\n',
    )

    assert parse_claude_code_config(path)[0].source_line == 3


def test_evidence_path_is_the_file_that_was_parsed(tmp_path: Path) -> None:
    path = write(tmp_path, '{"mcpServers": {"svc": {"command": "x"}}}')

    assert parse_claude_code_config(path)[0].source_file == path


def test_line_key_never_leaks_as_a_fake_server_or_env_var(tmp_path: Path) -> None:
    path = write(tmp_path, '{"mcpServers": {"svc": {"command": "x", "env": {"K": "v"}}}}')

    servers = parse_claude_code_config(path)

    assert all(s.name != "__line__" for s in servers)
    assert all("__line__" not in s.env_var_names for s in servers)
