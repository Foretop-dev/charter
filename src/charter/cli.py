import json
import sys
from pathlib import Path

import typer
from keel.git_ref import GitError, merge_base, toplevel
from rich.console import Console

from charter.brand import BRAND
from charter.collect import collect
from charter.drift import Drift, compute_drift
from charter.enumerate import EnumerationResult, enumerate_stdio_server
from charter.git_ref import show_file_at_ref
from charter.lockfile import to_manifest, write_lock
from charter.models import Server, ServerSet, Transport
from charter.render.annotations import render_annotations
from charter.render.markdown import render_markdown
from charter.render.sarif import render_sarif
from charter.render.terminal import render_drift, render_terminal

_FORMATS = ("table", "markdown", "sarif", "annotations")

app = typer.Typer(
    name="charter",
    help=f"{BRAND} charter — a reviewable record of every MCP server an agent config can reach.",
)
error_console = Console(stderr=True)


@app.callback()
def _callback() -> None:
    """Keeps `scan` addressable as `charter scan PATH` — without a callback, Typer collapses
    an app with a single command into a bare `charter PATH` invocation instead."""


def _enumerate_all(servers: tuple[Server, ...], timeout: float) -> dict[Server, EnumerationResult]:
    results: dict[Server, EnumerationResult] = {}
    for server in servers:
        if server.transport == Transport.STDIO:
            results[server] = enumerate_stdio_server(server, timeout=timeout)
    return results


def _drift_vs_base(
    root: Path, lock_path: Path, base: str, current_manifest: dict[str, object]
) -> tuple[Drift, ...]:
    """DEC-04's own framing ("charter.lock *is* the review mechanism") drives this: the
    baseline is the committed lock file's content at the merge base, read directly via `git
    show` (charter.git_ref.show_file_at_ref) — never a live re-scan of old configs the way
    telltale's worktree-based --base comparison works, since that would mean launching whatever
    third-party MCP server versions existed at an old commit.
    """
    try:
        repo_root = toplevel(root)
        base_commit = merge_base(repo_root, base)
    except GitError as exc:
        error_console.print(str(exc))
        raise typer.Exit(code=2) from exc

    try:
        relative_lock = lock_path.resolve().relative_to(repo_root)
    except ValueError:
        # --lock points outside the repo (or root itself isn't inside repo_root, which
        # toplevel() would already have raised on) — nothing committed to compare against, so
        # there's no baseline. Not an error: same as a brand new charter.lock's first commit.
        return ()

    baseline_text = show_file_at_ref(repo_root, base_commit, relative_lock)
    baseline = json.loads(baseline_text) if baseline_text is not None else None
    return compute_drift(baseline, current_manifest)


def _render(
    fmt: str,
    server_set: ServerSet,
    lock_path: Path,
    enumeration: dict[Server, EnumerationResult],
    drift: tuple[Drift, ...],
) -> str:
    no_color = not sys.stdout.isatty()
    if fmt == "table":
        output = render_terminal(server_set, lock_path, enumeration, no_color=no_color)
        if drift:
            output += render_drift(drift, no_color=no_color)
        return output
    if fmt == "markdown":
        return render_markdown(server_set, enumeration, drift)
    if fmt == "sarif":
        # `enumeration` is {} whenever --enumerate wasn't passed — render_sarif still returns a
        # valid SARIF log in that case (the full rule catalog, zero results), since there is
        # nothing to report rather than an error. SARIF stays a snapshot, no drift section —
        # same Session 16 call: SARIF is for tooling integration, not a narrative diff.
        return json.dumps(render_sarif(server_set, enumeration), indent=2, sort_keys=True)
    if fmt == "annotations":
        return render_annotations(drift)
    raise ValueError(f"unknown format: {fmt!r}")


@app.command()
def scan(
    path: Path = typer.Argument(Path("."), help="Repository path to scan."),  # noqa: B008
    lock: Path | None = typer.Option(  # noqa: B008
        None,
        "--lock",
        help="Path to write charter.lock to. Defaults to <path>/charter.lock.",
    ),
    fmt: str = typer.Option(
        "table", "--format", help="Output format: table, markdown, sarif, or annotations."
    ),
    enumerate_tools: bool = typer.Option(
        False,
        "--enumerate",
        help="Launch each stdio server locally and call its real tools/list. DEC-02: this "
        "runs third-party code, so it is opt-in, never the default. Remote (http/sse/ws) "
        "servers are not enumerated yet.",
    ),
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        help="Per-server timeout in seconds for --enumerate.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help="Git ref to compare against (e.g. origin/main). Exits 1 only if a server or tool "
        "capability is new or more severe than at the merge base with this ref — never an "
        "absolute threshold. Needs --enumerate on both sides for tool-level drift; without "
        "it, only new-server drift is detectable.",
    ),
) -> None:
    """Parse committed MCP client configs (.mcp.json, .cursor/mcp.json) under `path` and write
    a deterministic charter.lock recording every declared server, its transport, and the
    *names* (never values — DEC-06) of any credential-referencing env vars or headers it
    wires in. Static parsing only by default: no server is ever launched, no network call is
    made, unless --enumerate is passed.
    """
    if fmt not in _FORMATS:
        error_console.print(f"Unknown --format {fmt!r}. Choose one of: {', '.join(_FORMATS)}.")
        raise typer.Exit(code=2)

    root = path.resolve()
    lock_path = (lock.resolve() if lock else root / "charter.lock").resolve()

    server_set = collect(root)
    enumeration = _enumerate_all(server_set.servers, timeout) if enumerate_tools else {}
    write_lock(server_set, root, lock_path, enumeration)

    drift: tuple[Drift, ...] = ()
    if base is not None:
        current_manifest = to_manifest(server_set, root, enumeration)
        drift = _drift_vs_base(root, lock_path, base, current_manifest)

    output = _render(fmt, server_set, lock_path, enumeration, drift)
    if output:
        print(output)

    if drift:
        raise typer.Exit(code=1)
