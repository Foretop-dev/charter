from pathlib import Path

from charter.models import Transport
from charter.parsers.cursor import parse_cursor_config


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(content)
    return path


def test_parses_a_stdio_server(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "local": {
      "command": "python",
      "args": ["server.py"],
      "env": {"API_KEY": "should-never-appear-in-output"}
    }
  }
}
""",
    )

    servers = parse_cursor_config(path)

    assert len(servers) == 1
    server = servers[0]
    assert server.name == "local"
    assert server.transport == Transport.STDIO
    assert server.command == "python"
    assert server.args == ("server.py",)
    assert server.env_var_names == ("API_KEY",)


def test_env_values_are_never_captured(tmp_path: Path) -> None:
    path = write(
        tmp_path, '{"mcpServers": {"svc": {"command": "x", "env": {"K": "secret-value-123"}}}}'
    )

    server = parse_cursor_config(path)[0]

    assert "secret-value-123" not in repr(server)


def test_parses_a_remote_server_without_an_explicit_type(tmp_path: Path) -> None:
    # Cursor's config shape has no `type` field — presence of `url` alone means remote.
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "remote": {
      "url": "https://api.example.com/mcp",
      "headers": {"API_KEY": "secret-header-value"}
    }
  }
}
""",
    )

    server = parse_cursor_config(path)[0]

    assert server.transport == Transport.HTTP
    assert server.url == "https://api.example.com/mcp"
    assert server.header_names == ("API_KEY",)
    assert server.command is None


def test_header_values_are_never_captured(tmp_path: Path) -> None:
    path = write(
        tmp_path, '{"mcpServers": {"svc": {"url": "https://x", "headers": {"K": "secret-v"}}}}'
    )

    server = parse_cursor_config(path)[0]

    assert "secret-v" not in repr(server)


def test_a_stdio_entry_with_no_command_is_skipped(tmp_path: Path) -> None:
    path = write(tmp_path, '{"mcpServers": {"broken": {"args": ["-y"]}}}')

    assert parse_cursor_config(path) == ()


def test_envfile_and_auth_are_not_read_yet(tmp_path: Path) -> None:
    # Documented gap (see parse_cursor_config's docstring) — the server still parses using the
    # fields it does read, it just doesn't surface envFile/auth as evidence yet.
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "svc": {"command": "x", "envFile": ".env"},
    "remote": {"url": "https://y", "auth": {"CLIENT_SECRET": "s"}}
  }
}
""",
    )

    servers = parse_cursor_config(path)

    assert len(servers) == 2


def test_multiple_servers_are_all_parsed(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """{
  "mcpServers": {
    "a": {"command": "x"},
    "b": {"url": "https://y"}
  }
}
""",
    )

    assert {s.name for s in parse_cursor_config(path)} == {"a", "b"}


def test_missing_mcp_servers_key_yields_no_servers(tmp_path: Path) -> None:
    path = write(tmp_path, '{"title": "empty"}')

    assert parse_cursor_config(path) == ()


def test_invalid_json_degrades_to_no_servers_not_a_crash(tmp_path: Path) -> None:
    path = write(tmp_path, "{ not valid [")

    assert parse_cursor_config(path) == ()


def test_evidence_line_points_at_the_server_entry(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '{\n  "mcpServers": {\n    "svc": {\n      "command": "x"\n    }\n  }\n}\n',
    )

    assert parse_cursor_config(path)[0].source_line == 3


def test_line_key_never_leaks_as_a_fake_server_or_env_var(tmp_path: Path) -> None:
    path = write(tmp_path, '{"mcpServers": {"svc": {"command": "x", "env": {"K": "v"}}}}')

    servers = parse_cursor_config(path)

    assert all(s.name != "__line__" for s in servers)
    assert all("__line__" not in s.env_var_names for s in servers)
