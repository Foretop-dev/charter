from collections.abc import Callable
from pathlib import Path

from charter.models import Server, ServerSet
from charter.parsers.claude_code import parse_claude_code_config
from charter.parsers.cursor import parse_cursor_config

# Project-root, committed config files only — DEC-04: "the review is a code review, which is
# the whole product." A per-machine user config (Claude Code's ~/.claude.json local/user
# scopes, claude_desktop_config.json) is never part of the codebase this tool reviews, so it's
# deliberately not one of these candidates — same reasoning telltale's own autodetect_spec_path
# uses for a small, fixed candidate list rather than a full repo walk (specs/charter.md DEC-05
# doesn't call for scattered discovery any more than telltale's own OpenAPI spec does).
_CONFIG_SOURCES: tuple[tuple[str, Callable[[Path], tuple[Server, ...]]], ...] = (
    (".mcp.json", parse_claude_code_config),
    (".cursor/mcp.json", parse_cursor_config),
)


def collect(root: Path) -> ServerSet:
    """Checks each known, committed MCP client config path under `root` and parses whichever
    exist — both, one, or neither. Not a repo walk: agent configs live at a small number of
    fixed, well-known locations (confirmed live against Claude Code's and Cursor's own docs
    this session), the same reasoning that keeps telltale's OpenAPI spec autodetection to a
    fixed candidate list rather than a full `keel.collect.walk` traversal.
    """
    root = root.resolve()
    servers: list[Server] = []
    for relative_path, parse in _CONFIG_SOURCES:
        path = root / relative_path
        if path.is_file():
            servers.extend(parse(path))
    return ServerSet(servers=tuple(servers))
