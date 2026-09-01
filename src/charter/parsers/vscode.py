"""`.vscode/mcp.json` — VS Code's own workspace MCP configuration.

R22, and the third format `specs/charter.md` DEC-01 named. Its option text was "Claude Desktop /
Claude Code config · **VS Code + Cursor** · custom agent frameworks", and the chosen default
built the Claude and Cursor formats only. VS Code was in the sentence and never implemented, so
a repository configuring its agent through VS Code was indistinguishable from one declaring no
agent configuration at all.

The difference from the other two is one key, and it is load-bearing: VS Code wraps its servers
in **`servers`**, where Claude Code and Cursor both use `mcpServers`. Confirmed against
code.visualstudio.com's own MCP documentation fetched live on 2026-09-01, and against real
committed files (dotCMS/core, getsentry/sentry-mcp, Azure-Samples/azure-ai-travel-agents,
equinor/design-system). This parser deliberately does *not* also accept `mcpServers`: doing so
would make the three parsers interchangeable and the format distinction meaningless.

The per-entry shape is the same one Cursor uses — `command`/`args`/`env` for a local server,
`url`/`headers` for a remote one, with `type` optional and the deciding field being which of
`url` or `command` is present — so `parsers.cursor._server_from_entry` is reused rather than
reimplemented. VS Code does write an explicit `"type": "stdio"` more often than Cursor does, but
nothing depends on it being there: equinor/design-system omits it for one entry and sets it for
another in the same document.

Not read, and left as a documented gap rather than half-modelled: the `inputs` array, a sibling
of `servers` that declares credential prompts (`"type": "promptString", "password": true`)
referenced from a server's own fields as `${input:id}`. The credential *reference* is already
visible through `header_names`/`env_var_names`, which is what charter reports on; modelling the
prompt definitions themselves is the same kind of separate signal Cursor's own `envFile` and
`auth` fields are, and they are deferred for the same reason.
"""

from pathlib import Path

import yaml
from keel.collect.line_tracking import parse_with_lines

from charter.models import Server
from charter.parsers.cursor import _server_from_entry


def parse_vscode_config(path: Path) -> tuple[Server, ...]:
    """Degrades rather than crashes: a file that isn't valid JSON, or whose top level has no
    `servers` object, produces zero servers for this file."""
    try:
        document = parse_with_lines(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ()
    if not isinstance(document, dict):
        return ()

    servers = document.get("servers")
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
