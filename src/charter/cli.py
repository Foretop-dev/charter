import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from keel.finding import Verdict
from keel.gate import GateFetchError, gate_identities, maybe_fetch_gate, summarize
from keel.git_ref import GitError, merge_base, toplevel
from keel.render.triage_json import render_triage_json
from keel.report import ReportError, maybe_report
from keel.triage import TriageResult
from rich.console import Console

from charter.brand import BRAND
from charter.collect import collect
from charter.drift import Drift, compute_drift
from charter.enumerate import EnumerationResult, enumerate_stdio_server
from charter.findings import to_findings
from charter.git_ref import show_file_at_ref
from charter.lockfile import to_manifest, write_lock
from charter.models import Server, ServerSet, Transport
from charter.render.annotations import render_annotations
from charter.render.json_renderer import render_json
from charter.render.markdown import render_markdown
from charter.render.sarif import render_sarif
from charter.render.terminal import render_drift, render_terminal
from charter.sandbox import SandboxUnavailableError
from charter.triage import build_triage

_FORMATS = ("table", "markdown", "json", "sarif", "annotations", "triage-json")

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
    root: Path,
    *,
    triage: TriageResult | None = None,
) -> str:
    no_color = not sys.stdout.isatty()
    if fmt == "table":
        output = render_terminal(server_set, lock_path, enumeration, no_color=no_color)
        if drift:
            output += render_drift(drift, no_color=no_color)
        return output
    if fmt == "markdown":
        return render_markdown(server_set, enumeration, drift)
    if fmt == "json":
        # The suite-wide Finding envelope (keel.finding), identical to ebb's and telltale's own
        # --format json — not a charter-shaped server/tool dump. That is what makes one hosted
        # dashboard, one digest and one baseline format possible across products.
        return render_json(to_findings(server_set, enumeration, drift, root))
    if fmt == "sarif":
        # `enumeration` is {} whenever --enumerate wasn't passed — render_sarif still returns a
        # valid SARIF log in that case (the full rule catalog, zero results), since there is
        # nothing to report rather than an error. SARIF stays a snapshot, no drift section —
        # same Session 16 call: SARIF is for tooling integration, not a narrative diff.
        return json.dumps(render_sarif(server_set, enumeration), indent=2, sort_keys=True)
    if fmt == "annotations":
        return render_annotations(drift)
    if fmt == "triage-json":
        assert triage is not None  # guaranteed by the fmt == "triage-json" caller-side guard
        return render_triage_json(triage)
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
        "table",
        "--format",
        help="Output format: table, markdown, json, sarif, annotations, or triage-json.",
    ),
    enumerate_tools: bool = typer.Option(
        False,
        "--enumerate",
        help="On Linux, launch each stdio server inside Charter's fail-closed Bubblewrap "
        "sandbox and call its real tools/list. The repository is read-only and the server "
        "gets no network or configured credentials. Remote servers are not enumerated.",
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
    report: bool = typer.Option(
        False,
        "--report",
        help="Send this run's findings to the hub (FORETOP_TOKEN required). Off by default. "
        "Always prints the exact payload before sending — findings are metadata only, "
        "never your source.",
    ),
    gate: bool = typer.Option(
        False,
        "--gate",
        help="Fetch the org's baseline/suppressions from the hub (FORETOP_TOKEN required) and "
        "exclude matching drift from the exit-1 decision. Off by default; a no-op unless "
        "--base is also given.",
    ),
) -> None:
    """Parse committed MCP client configs (.mcp.json, .cursor/mcp.json) under `path` and write
    a deterministic charter.lock recording every declared server, its transport, and the
    *names* (never values — DEC-06) of any credential-referencing env vars or headers it
    wires in. Static parsing only by default: no server is ever launched, no network call is
    made. --enumerate launches local stdio servers without network access and only when the
    required Linux/Bubblewrap isolation boundary is available.
    """
    if fmt not in _FORMATS:
        error_console.print(f"Unknown --format {fmt!r}. Choose one of: {', '.join(_FORMATS)}.")
        raise typer.Exit(code=2)

    root = path.resolve()
    lock_path = (lock.resolve() if lock else root / "charter.lock").resolve()

    server_set = collect(root)
    try:
        enumeration = _enumerate_all(server_set.servers, timeout) if enumerate_tools else {}
    except SandboxUnavailableError as exc:
        error_console.print(str(exc))
        raise typer.Exit(code=2) from exc
    write_lock(server_set, root, lock_path, enumeration)

    drift: tuple[Drift, ...] = ()
    if base is not None:
        current_manifest = to_manifest(server_set, root, enumeration)
        drift = _drift_vs_base(root, lock_path, base, current_manifest)

    # Computed lazily, shared between --format triage-json and --report — a plain scan with
    # neither flag must never pay for it, and giving both at once must not compute it twice.
    triage = (
        build_triage(server_set, enumeration, drift, root)
        if fmt == "triage-json" or report
        else None
    )
    output = _render(fmt, server_set, lock_path, enumeration, drift, root, triage=triage)
    if output:
        print(output)

    if report:
        try:
            findings = to_findings(server_set, enumeration, drift, root)
            maybe_report(
                enabled=True, product="charter", path=path, findings=findings, triage=triage
            )
        except ReportError as exc:
            error_console.print(str(exc))
            raise typer.Exit(code=2) from exc

    candidates = {
        f.identity
        for f in to_findings(server_set, enumeration, drift, root)
        if f.verdict == Verdict.BREAK
    }
    # --gate is a no-op both when unset and when there's no drift to begin with — no network
    # call in either case. Unlike telltale's Regression, a single Drift on one tool marks
    # *every* capability finding for that tool BREAK (_tool_capability_findings' own
    # documented imprecision), so identities are derived from the real to_findings() output
    # (confirmed the only source of BREAK in charter's own findings.py) rather than
    # hand-rederiving from Drift objects directly.
    if gate and candidates:
        try:
            gate_response = maybe_fetch_gate(enabled=True)
        except GateFetchError as exc:
            error_console.print(str(exc))
            raise typer.Exit(code=2) from exc
        assert gate_response is not None  # enabled=True always returns a GateResponse
        result = gate_identities(candidates, gate_response, today=datetime.now(UTC).date())
        print(summarize(result))
        candidates -= result.excluded
    if candidates:
        raise typer.Exit(code=1)
