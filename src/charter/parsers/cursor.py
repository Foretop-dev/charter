from pathlib import Path
from typing import Any

import yaml
from keel.collect.line_tracking import line_of, parse_with_lines

from charter.models import Server, Transport


def parse_cursor_config(path: Path) -> tuple[Server, ...]:
    """`.cursor/mcp.json` (project scope — cursor.com/docs/context/mcp, fetched live this
    session). Same `mcpServers` wrapper key as Claude Code's format, but no explicit `type`
    field: which shape an entry is comes from which required field it has — `command` means
    STDIO, `url` means remote. Cursor's own docs don't expose a separate SSE/WebSocket type the
    way Claude Code's `type` field does, so every remote entry here is recorded as `http` — a
    real, documented limitation of what this client's config format itself reveals, not a
    guess charter is making.

    `envFile` (a path to a `.env` file for a stdio server) and `auth` (an OAuth client
    id/secret/scopes object for a remote server) are real fields in Cursor's format that this
    parser doesn't read yet — DEC-06 treats "credential file paths" as a distinct signal from
    "credential env var names", and `auth.CLIENT_SECRET` is itself credential-shaped; both are
    left for a later slice rather than half-modeled now. A documented gap, not a silent one.

    Degrades rather than crashes: a file that isn't valid JSON, or whose top level has no
    `mcpServers` object, produces zero servers for this file.
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
    source_line = line_of(entry)
    url = entry.get("url")
    if url is not None:
        if not isinstance(url, str):
            return None
        return Server(
            name=name,
            transport=Transport.HTTP,
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
