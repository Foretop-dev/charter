import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git operation needed for a --base-style comparison fails — not a git
    repo, an unknown ref, or git itself isn't on PATH. Extracted from telltale's own
    git_ref.py (Session 15's `keel.collect.line_tracking` extraction set the bar this follows:
    charter needed the identical thing, not merely something similar) — kept distinct from any
    app's own domain errors so each CLI can give a precise, different exit-2 message per
    failure mode."""


def toplevel(path: Path) -> Path:
    """The repository's actual root, which `path` may be any subdirectory of — a monorepo scan
    (e.g. `telltale check apps/telltale`, run from within a larger checkout) needs this: `git
    worktree add` always materializes the *whole* repository, never a subtree, so a caller that
    only asked to scan a subdirectory needs to know the offset between that subdirectory and
    the checkout root to find its own files again inside a new worktree — and a caller reading
    a file at a historical ref via `git show <rev>:<path>` needs the same root to resolve a
    repo-relative path from an absolute one."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(f"{path} is not inside a git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def merge_base(repo_root: Path, ref: str) -> str:
    """The commit where the current checkout and `ref` diverged — every product in this suite
    that gates on "did this get worse" compares against the merge base, not `ref`'s current
    tip, which can have moved since a PR branched."""
    result = subprocess.run(
        ["git", "merge-base", "HEAD", ref],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(f"git merge-base HEAD {ref!r} failed: {result.stderr.strip()}")
    return result.stdout.strip()
