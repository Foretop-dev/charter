import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from charter.cli import app

runner = CliRunner()
ARGUMENT_SECRET = "postgresql://readonly:argument-password-9fb1@example.invalid/service"


def _write_service(root: Path) -> None:
    config = {
        "mcpServers": {
            "database": {
                "command": "database-mcp",
                "args": ["--dsn", ARGUMENT_SECRET],
                "env": {"DATABASE_TOKEN": "environment-value-must-not-appear"},
            }
        }
    }
    (root / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_argument_secrets_are_structurally_excluded_from_every_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_service(tmp_path)
    reports: list[dict[str, object]] = []
    monkeypatch.setattr("charter.cli.maybe_report", lambda **kwargs: reports.append(kwargs))

    outputs: list[str] = []
    for fmt in ("table", "markdown", "json", "sarif", "annotations", "triage-json"):
        result = runner.invoke(app, ["scan", str(tmp_path), "--format", fmt])
        assert result.exit_code == 0
        outputs.append(result.output)

    report_result = runner.invoke(app, ["scan", str(tmp_path), "--report"])
    assert report_result.exit_code == 0
    outputs.append(report_result.output)

    lock_text = (tmp_path / "charter.lock").read_text(encoding="utf-8")
    manifest = json.loads(lock_text)
    server = manifest["servers"][0]

    assert manifest["schema_version"] == 4
    assert "args" not in server
    assert server["arg_count"] == 2
    assert ARGUMENT_SECRET not in lock_text
    assert all(ARGUMENT_SECRET not in output for output in outputs)
    assert ARGUMENT_SECRET not in repr(reports)


def test_schema_v4_scan_remains_compatible_with_a_schema_v3_baseline(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _write_service(repo)
    schema_v3 = {
        "schema_version": 3,
        "servers": [
            {
                "name": "database",
                "transport": "stdio",
                "command": "database-mcp",
                "args": ["--dsn", ARGUMENT_SECRET],
                "env_var_names": ["DATABASE_TOKEN"],
                "url": None,
                "header_names": [],
                "source_file": ".mcp.json",
                "source_line": 1,
                "tools": None,
                "enumeration_error": None,
            }
        ],
    }
    (repo / "charter.lock").write_text(json.dumps(schema_v3), encoding="utf-8")
    _git(repo, "add", ".mcp.json", "charter.lock")
    _git(repo, "commit", "-q", "-m", "base: schema v3 lock")
    base_branch = _git(repo, "branch", "--show-current")
    _git(repo, "checkout", "-q", "-b", "feature")

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch])

    assert result.exit_code == 0
    current_lock = (repo / "charter.lock").read_text(encoding="utf-8")
    current_manifest = json.loads(current_lock)
    assert current_manifest["schema_version"] == 4
    assert ARGUMENT_SECRET not in current_lock
