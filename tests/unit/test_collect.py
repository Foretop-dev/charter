from pathlib import Path

from charter.collect import collect
from charter.models import Transport


def test_reads_a_claude_code_config_at_the_project_root(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"a": {"command": "x"}}}')

    result = collect(tmp_path)

    assert len(result.servers) == 1
    assert result.servers[0].name == "a"


def test_reads_a_cursor_config_at_dot_cursor_mcp_json(tmp_path: Path) -> None:
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": {"b": {"command": "y"}}}')

    result = collect(tmp_path)

    assert len(result.servers) == 1
    assert result.servers[0].name == "b"


def test_reads_both_when_both_are_present(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"a": {"command": "x"}}}')
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": {"b": {"url": "https://y"}}}')

    result = collect(tmp_path)

    names = {s.name for s in result.servers}
    assert names == {"a", "b"}


def test_neither_present_yields_an_empty_server_set(tmp_path: Path) -> None:
    result = collect(tmp_path)

    assert result.servers == ()


def test_a_claude_desktop_style_config_at_the_root_is_not_read(tmp_path: Path) -> None:
    # Deliberately not one of the candidates — claude_desktop_config.json is a per-machine
    # user file, never committed, so even one that happens to sit at the scan root (an unusual
    # but possible layout) shouldn't be treated as this repo's own reviewable config.
    (tmp_path / "claude_desktop_config.json").write_text('{"mcpServers": {"a": {"command": "x"}}}')

    result = collect(tmp_path)

    assert result.servers == ()


def test_same_server_name_in_both_files_produces_two_distinct_entries(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"shared": {"command": "x"}}}')
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"shared": {"command": "different"}}}'
    )

    result = collect(tmp_path)

    assert len(result.servers) == 2
    assert {s.command for s in result.servers} == {"x", "different"}


def test_transport_is_correctly_recorded_from_each_source(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"remote": {"type": "http", "url": "https://x"}}}'
    )

    result = collect(tmp_path)

    assert result.servers[0].transport == Transport.HTTP


def test_all_three_committed_config_formats_are_collected_together(tmp_path: Path) -> None:
    """R22: a real team may use more than one client. Cursor and VS Code configs coexist in
    plenty of repositories, and each declares its own servers, so collect must read every
    format present rather than stopping at the first file it finds."""
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"from-claude": {"command": "npx", "args": []}}}', encoding="utf-8"
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"from-cursor": {"command": "npx", "args": []}}}', encoding="utf-8"
    )
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        '{"servers": {"from-vscode": {"command": "npx", "args": []}}}', encoding="utf-8"
    )

    server_set = collect(tmp_path)

    assert {s.name for s in server_set.servers} == {"from-claude", "from-cursor", "from-vscode"}


def test_a_vscode_only_repository_is_no_longer_invisible(tmp_path: Path) -> None:
    """The gap R22 closed: before `.vscode/mcp.json` was a candidate, a repository configuring
    its agent through VS Code looked exactly like one declaring no agent configuration at
    all."""
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").write_text(
        '{"servers": {"nx-mcp": {"type": "stdio", "command": "npx", "args": ["-y", "nx-mcp"]}}}',
        encoding="utf-8",
    )

    server_set = collect(tmp_path)

    assert [s.name for s in server_set.servers] == ["nx-mcp"]
