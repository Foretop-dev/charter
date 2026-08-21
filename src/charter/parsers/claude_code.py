from pathlib import Path
from typing import Any

import yaml
from keel.collect.line_tracking import line_of, parse_with_lines

from charter.models import Server, Transport

# code.claude.com/docs/en/mcp (fetched live this session), "Option 1/2/4" + "From an mcpServers
# JSON block": a `url`-having entry needs an explicit `type` naming the transport — Claude Code
# itself treats a `url` with no `type` as a configuration error and skips that server entirely
# ("MCP server "<name>" has a "url" but no "type"..."). This parser mirrors that real behavior
# exactly rather than guessing a transport Claude Code itself wouldn't have used — an entry
# charter can't confidently attribute to a transport contributes no Server, honestly, same
# "degrade rather than fabricate" discipline as telltale's own parsers.
_URL_TYPE_TO_TRANSPORT: dict[str, Transport] = {
    "http": Transport.HTTP,
    # "streamable-http" is the MCP spec's own name for this transport; Claude Code accepts it
    # as an alias for "http" so configs copied from a server's own docs work unmodified.
    "streamable-http": Transport.HTTP,
    "sse": Transport.SSE,
    "ws": Transport.WS,
}


def parse_claude_code_config(path: Path) -> tuple[Server, ...]:
    """`.mcp.json` (project scope — the format meant to be committed and reviewed, DEC-04) and
    the same `mcpServers` shape Claude Code also reads from `~/.claude.json`'s local/user
    scopes, though this parser is only ever pointed at repo-committed files by
    `charter/collect.py`; per-machine `~/.claude.json` entries aren't part of the codebase
    being reviewed. Degrades rather than crashes: a file that isn't valid JSON, or whose top
    level has no `mcpServers` object, produces zero servers for this file.
    """
    try:
        document = parse_with_lines(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ()
    if not isinstance(document, dict):
        return ()

    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        return ()

    results: list[Server] = []
    for name, entry in servers.items():
        if name == "__line__" or not isinstance(entry, dict):
            continue
        server = _server_from_entry(name, entry, path)
        if server is not None:
            results.append(server)
    return tuple(results)


def _names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    return tuple(sorted(k for k in value if k != "__line__" and isinstance(k, str)))


def _server_from_entry(name: str, entry: dict[str, Any], path: Path) -> Server | None:
    url = entry.get("url")
    source_line = line_of(entry)

    if url is not None:
        if not isinstance(url, str):
            return None
        type_field = entry.get("type")
        if not isinstance(type_field, str) or type_field not in _URL_TYPE_TO_TRANSPORT:
            # No `type`, or a `type` Claude Code itself wouldn't recognize — a real
            # configuration error, not evidence of a working server. See module docstring.
            return None
        return Server(
            name=name,
            transport=_URL_TYPE_TO_TRANSPORT[type_field],
            command=None,
            args=(),
            env_var_names=(),
            url=url,
            header_names=_names(entry.get("headers")),
            source_file=path,
            source_line=source_line,
        )

    command = entry.get("command")
    if not isinstance(command, str):
        return None
    args = entry.get("args")
    args_tuple = tuple(a for a in args if isinstance(a, str)) if isinstance(args, list) else ()
    return Server(
        name=name,
        transport=Transport.STDIO,
        command=command,
        args=args_tuple,
        env_var_names=_names(entry.get("env")),
        url=None,
        header_names=(),
        source_file=path,
        source_line=source_line,
    )
