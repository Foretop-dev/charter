import subprocess
from pathlib import Path

__all__ = ["show_file_at_ref"]


def show_file_at_ref(repo_root: Path, rev: str, relative_path: Path) -> str | None:
    """Reads `relative_path` as it existed at `rev`, via `git show {rev}:{relative_path}` —
    DEC-04's own framing ("charter.lock *is* the review mechanism") is why the drift gate
    (charter/drift.py) reads the committed lock file directly instead of re-scanning old
    configs the way telltale's git_ref.checkout_ref does: simpler, and it never launches
    whatever third-party MCP server versions existed at an old commit.

    `rev` is always a commit SHA that already came from a successful `keel.git_ref.merge_base`
    call, so a non-zero exit here overwhelmingly means one thing: this path didn't exist at
    that commit (the first time charter.lock was ever committed, or a non-default `--lock`
    path) — returned as `None`, not raised. `drift.compute_drift` already treats `None` as "no
    baseline, nothing to compare" rather than "everything is new" (the same null/empty/
    populated three-state discipline `lockfile.py`'s own `tools: null` encodes), so a brand
    new charter.lock never fails a PR's very first drift check.
    """
    result = subprocess.run(
        ["git", "show", f"{rev}:{relative_path.as_posix()}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout
