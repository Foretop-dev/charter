from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from charter.capability import Severity, classify_tool
from charter.drift import Drift
from charter.enumerate import EnumerationResult
from charter.models import Server, ServerSet

_SEVERITY_STYLE = {
    Severity.LOW: "green",
    Severity.MEDIUM: "yellow",
    Severity.HIGH: "red",
    Severity.CRITICAL: "bold red",
}


def _tool_label(tool_name: str, severity: Severity, no_color: bool) -> str:
    # tool_name is not this tool's own to trust — it comes verbatim from a live third-party
    # MCP server's own tools/list response (DEC-02's whole point: "launching third-party
    # servers is a security decision"), so it's escaped with Rich's own markup.escape rather
    # than trusted not to contain "[...]" sequences that would otherwise be parsed as style
    # tags. Found the hard way: an early version only escaped this function's own literal
    # "[low]"/"[high]" suffix and left tool_name itself unescaped — Rich's Table silently
    # swallows an unrecognized style tag rather than erroring, so a tool whose real name (or,
    # before this fix, even the plain-text severity suffix in the *no_color* branch) contained
    # "[...]" would have vanished from the rendered table with no error at all. Verified by
    # reproducing the exact swallow against a bare Rich Table before writing the fix.
    safe_name = escape(tool_name)
    if no_color:
        return f"{safe_name} \\[{severity.value}]"
    style = _SEVERITY_STYLE[severity]
    return f"{safe_name} [{style}]\\[{severity.value}][/{style}]"


def _tools_cell(
    server: Server, enumeration: dict[Server, EnumerationResult], no_color: bool
) -> str:
    result = enumeration.get(server)
    if result is None:
        return "—"
    if result.error is not None:
        # The error string can itself embed untrusted text (e.g. a JSON-RPC error message the
        # server chose) — escaped for the same reason tool names are.
        message = f"error: {escape(result.error)}"
        return message if no_color else f"[red]{message}[/red]"
    if not result.tools:
        return "0"
    labels = sorted(
        (_tool_label(t.name, classify_tool(t).severity, no_color) for t in result.tools),
        key=str,
    )
    return ", ".join(labels)


def render_terminal(
    servers: ServerSet,
    lock_path: Path,
    enumeration: dict[Server, EnumerationResult] | None = None,
    *,
    no_color: bool = False,
) -> str:
    """A minimal summary table for `charter scan`'s own console output — Slice 1's job is a
    trustworthy `charter.lock`, not a full render set (markdown/SARIF/annotations for CI are
    Slice 3/4, same as ebb's and telltale's own build order). Only ever shows env var / header
    *names* (Server itself carries nothing else — DEC-06), never a value.

    The Tools column is "—" when `--enumerate` wasn't passed for that server (not attempted,
    the same "don't conflate absence of evidence with evidence of absence" distinction
    lockfile.py's `tools: null` makes), "0" when it was attempted and the server genuinely
    advertised none, an error message when the attempt failed, or each real tool name
    annotated with its DEC-03 rules-only severity (`classify_tool`, `[low]`/`[medium]`/
    `[high]`/`[critical]`) — never just the bare name, since severity is the whole reason a
    reviewer is looking at this table.

    Every dynamic value that reaches Rich's `Table.add_row` is escaped with `rich.markup.
    escape` — server-config-sourced strings (name/command/url/env var names/header names) and,
    especially, anything that came from a live third-party server's own response (a tool's
    name, an enumeration error message) are never trusted not to contain "[...]" sequences
    Rich would otherwise parse as style markup.

    `no_color`/`force_terminal` follow the same pattern ebb's and telltale's own terminal
    renderers settled on: a Console writing into an internal StringIO is never itself a real
    tty, so Rich's own terminal autodetection can't be trusted — the caller's real isatty()
    decision is passed in explicitly and force_terminal makes it authoritative either way.
    """
    enumeration = enumeration or {}
    table = Table(title="charter — MCP servers")
    table.add_column("Name")
    table.add_column("Transport")
    table.add_column("Command / URL")
    table.add_column("Env vars")
    table.add_column("Headers")
    table.add_column("Tools")

    for server in sorted(servers.servers, key=lambda s: (s.name, str(s.source_file))):
        reach = escape(server.command or server.url or "—")
        env = escape(", ".join(server.env_var_names)) or "—"
        headers = escape(", ".join(server.header_names)) or "—"
        tools = _tools_cell(server, enumeration, no_color)
        table.add_row(escape(server.name), server.transport.value, reach, env, headers, tools)

    buffer = StringIO()
    console = Console(
        file=buffer,
        width=120,
        no_color=no_color,
        highlight=not no_color,
        force_terminal=not no_color,
    )
    console.print(table)
    console.print(f"{len(servers.servers)} server(s) — wrote {lock_path}")
    return buffer.getvalue()


def render_drift(drift: tuple[Drift, ...], *, no_color: bool = False) -> str:
    """Appended after `render_terminal`'s own output when `--base` found drift — a local
    `charter scan --base main` run that exits 1 shouldn't leave a reviewer with zero
    explanation in the default table view. Only called by the CLI when drift is non-empty
    (mirrors apps/telltale/src/telltale/render/terminal.py's own `render_regressions`)."""
    table = Table(title="charter — drift vs merge base")
    table.add_column("Server")
    table.add_column("Tool")
    table.add_column("Kind")
    table.add_column("Before → After")

    for d in drift:
        before_after = f"{d.before_severity or '—'} → {d.after_severity or '—'}"
        row = (
            escape(d.server_name),
            escape(d.tool_name) if d.tool_name else "—",
            d.kind.value,
            before_after,
        )
        table.add_row(*(r if no_color else f"[red]{r}[/red]" for r in row))

    buffer = StringIO()
    console = Console(
        file=buffer,
        width=120,
        no_color=no_color,
        highlight=not no_color,
        force_terminal=not no_color,
    )
    console.print(table)
    console.print(f"{len(drift)} drift finding(s) vs merge base")
    return buffer.getvalue()
