import subprocess
from pathlib import Path

from charter.git_ref import show_file_at_ref


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")
    return repo


def commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content)
    run_git(repo, "add", filename)
    run_git(repo, "commit", "-q", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def test_reads_a_files_content_at_a_given_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = commit(repo, "charter.lock", "v1", "first")
    commit(repo, "charter.lock", "v2", "second")

    assert show_file_at_ref(repo, first, Path("charter.lock")) == "v1"


def test_returns_none_when_the_path_never_existed_at_that_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    first = commit(repo, "a.txt", "1", "first")

    assert show_file_at_ref(repo, first, Path("charter.lock")) is None


def test_returns_none_for_an_unknown_rev(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit(repo, "charter.lock", "v1", "first")

    assert show_file_at_ref(repo, "totally-not-a-real-rev", Path("charter.lock")) is None
