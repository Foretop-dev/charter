"""R22: `.vscode/mcp.json`, the third committed agent-config format.

`specs/charter.md` DEC-01's own option text named "Claude Desktop / Claude Code config · **VS
Code + Cursor**" and the default picked "the Claude + Cursor config formats" — VS Code was in
the sentence and never built, so a repository that configures its agent through VS Code looked
to charter exactly like one with no agent configuration at all.

It is a real format with a real difference, not a rename: the top-level key is `servers`, not
Claude Code's and Cursor's `mcpServers`. Confirmed against code.visualstudio.com's own MCP
documentation fetched live on 2026-09-01, and against real committed files — dotCMS/core (949
stars), getsentry/sentry-mcp, Azure-Samples/azure-ai-travel-agents, equinor/design-system.
Every fixture below is reduced from one of those, not invented.
"""

from pathlib import Path

from charter.models import Transport
from charter.parsers.vscode import parse_vscode_config


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "mcp.json"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_top_level_key_is_servers_not_mcp_servers(tmp_path: Path) -> None:
    """The one difference that matters. A parser that looked for `mcpServers` here would find
    nothing and report the repository as having no agent configuration."""
    path = write(
        tmp_path,
        """
        {
          "servers": {
            "nx-mcp": {
              "type": "stdio",
              "command": "npx",
              "args": ["-y", "nx-mcp@latest"]
            }
          }
        }
        """,
    )

    (server,) = parse_vscode_config(path)

    assert server.name == "nx-mcp"
    assert server.transport is Transport.STDIO
    assert server.command == "npx"
    assert server.args == ("-y", "nx-mcp@latest")


def test_an_mcp_servers_key_here_is_not_read(tmp_path: Path) -> None:
    """The counterweight: this parser is for VS Code's format specifically. Accepting both keys
    would make the three parsers interchangeable and the format distinction meaningless."""
    path = write(tmp_path, '{"mcpServers": {"x": {"command": "npx", "args": []}}}')

    assert parse_vscode_config(path) == ()


def test_an_http_server_is_read_with_its_header_names(tmp_path: Path) -> None:
    """Reduced from Azure-Samples/azure-ai-travel-agents, which wires an Authorization header
    from a `${input:...}` reference. The header *name* is captured and the value never is —
    DEC-06, same as every other parser here."""
    path = write(
        tmp_path,
        """
        {
          "servers": {
            "mcp-echo-ping": {
              "type": "http",
              "url": "http://localhost:5004/mcp",
              "headers": {"Authorization": "Bearer ${input:toolEchoPingAccessToken}"}
            }
          }
        }
        """,
    )

    (server,) = parse_vscode_config(path)

    assert server.transport is Transport.HTTP
    assert server.url == "http://localhost:5004/mcp"
    assert server.header_names == ("Authorization",)
    assert server.command is None


def test_a_stdio_entry_without_an_explicit_type_is_still_stdio(tmp_path: Path) -> None:
    """`type` is optional in real files — equinor/design-system omits it for its playwright
    entry and sets it for its figma one, in the same document. Which shape an entry is comes
    from which field it carries, exactly as the Cursor parser already decides."""
    path = write(
        tmp_path,
        """
        {
          "servers": {
            "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
            "figma": {"type": "http", "url": "http://127.0.0.1:3845/mcp"}
          }
        }
        """,
    )

    servers = {s.name: s for s in parse_vscode_config(path)}

    assert servers["playwright"].transport is Transport.STDIO
    assert servers["figma"].transport is Transport.HTTP


def test_env_var_names_are_captured_without_their_values(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        {
          "servers": {
            "gh": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-github"],
              "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_notarealsecretvalue"}
            }
          }
        }
        """,
    )

    (server,) = parse_vscode_config(path)

    assert server.env_var_names == ("GITHUB_PERSONAL_ACCESS_TOKEN",)
    assert "ghp_notarealsecretvalue" not in repr(server)


def test_the_inputs_section_is_not_mistaken_for_a_server(tmp_path: Path) -> None:
    """VS Code's `inputs` array is a sibling of `servers`, not a server list. Reading it as one
    would invent servers out of credential prompts."""
    path = write(
        tmp_path,
        """
        {
          "inputs": [
            {"id": "token", "type": "promptString", "description": "token", "password": true}
          ],
          "servers": {"only": {"type": "http", "url": "https://example.invalid/mcp"}}
        }
        """,
    )

    servers = parse_vscode_config(path)

    assert [s.name for s in servers] == ["only"]


def test_a_malformed_or_empty_document_yields_no_servers(tmp_path: Path) -> None:
    """Degrades rather than crashes, same contract the other two parsers hold."""
    assert parse_vscode_config(write(tmp_path, "{ not valid json")) == ()
    assert parse_vscode_config(write(tmp_path, "[]")) == ()
    assert parse_vscode_config(write(tmp_path, "{}")) == ()
    assert parse_vscode_config(write(tmp_path, '{"servers": "nope"}')) == ()


def test_the_source_line_points_at_the_real_entry(tmp_path: Path) -> None:
    """Every finding charter emits carries a file and line a reviewer can open."""
    path = write(
        tmp_path,
        '{\n  "servers": {\n    "a": {"command": "x", "args": []}\n  }\n}\n',
    )

    (server,) = parse_vscode_config(path)

    assert server.source_file == path
    assert server.source_line == 3
