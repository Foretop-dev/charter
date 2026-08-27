import subprocess
from datetime import UTC, datetime
from pathlib import Path

from keel.git_ref import GitError, toplevel
from pydantic import BaseModel, ConfigDict


class Run(BaseModel):
    """Repo/branch/commit attribution for a viewing session — not a per-CLI-invocation
    record. Detected once, locally, by whatever reads a runs directory (`apps/hub`'s
    `foretop view`), never threaded through the five products' own `--format json` output.
    See `packages/keel/README.md` for why this scope was chosen over a richer, per-scan
    `Run` embedded in every product's CLI output.
    """

    model_config = ConfigDict(frozen=True)

    repo: str
    branch: str | None
    commit: str | None
    detected_at: datetime


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def detect_run(repo_root: Path) -> Run:
    """Never raises — a failure here is a recorded `None`, the same "a failure is a recorded
    status, never an exception" rule the rest of the suite follows. `repo_root` need not be a
    git repository at all: `repo` falls back to its own directory name, `branch`/`commit` stay
    `None`, rather than fabricating anything. `branch` can legitimately be `"HEAD"` in a
    detached-HEAD checkout — that's `git`'s own honest answer, kept as-is, not smoothed over.
    """
    detected_at = datetime.now(UTC)
    try:
        root = toplevel(repo_root)
    except (GitError, OSError):
        # OSError covers a repo_root that doesn't even exist — subprocess.run raises that
        # directly, before git ever runs, since it can't set the child process's cwd.
        return Run(
            repo=repo_root.resolve().name,
            branch=None,
            commit=None,
            detected_at=detected_at,
        )

    return Run(
        repo=root.name,
        branch=_run_git(["rev-parse", "--abbrev-ref", "HEAD"], root),
        commit=_run_git(["rev-parse", "HEAD"], root),
        detected_at=detected_at,
    )
