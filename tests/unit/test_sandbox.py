import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import charter.enumerate as enumerate_module
import charter.sandbox as sandbox_module
from charter.cli import app
from charter.sandbox import SandboxUnavailableError, build_bubblewrap_launch, require_bubblewrap

runner = CliRunner()


def test_enumeration_fails_closed_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    require_bubblewrap.cache_clear()
    monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Darwin")

    with pytest.raises(SandboxUnavailableError, match="Linux"):
        require_bubblewrap()


def test_enumeration_fails_closed_when_bubblewrap_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_bubblewrap.cache_clear()
    monkeypatch.setattr(sandbox_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox_module.shutil, "which", lambda _name: None)

    with pytest.raises(SandboxUnavailableError, match="Bubblewrap"):
        require_bubblewrap()


def test_cli_exits_2_before_launching_a_target_when_sandbox_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"hostile": {"command": "/usr/bin/false"}}})
    )

    def unavailable() -> str:
        raise SandboxUnavailableError("Bubblewrap is unavailable")

    def must_not_launch(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the configured target was launched without the required sandbox")

    monkeypatch.setattr(enumerate_module, "require_bubblewrap", unavailable)
    monkeypatch.setattr(enumerate_module.subprocess, "Popen", must_not_launch)

    result = runner.invoke(app, ["scan", str(tmp_path), "--enumerate"])

    assert result.exit_code == 2
    assert "Bubblewrap is unavailable" in result.output
    assert not (tmp_path / "charter.lock").exists()


def test_bubblewrap_policy_is_read_only_networkless_and_environment_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    secret = "must-not-cross-the-sandbox-boundary"
    monkeypatch.setenv("CHARTER_HOST_SECRET", secret)
    monkeypatch.setenv(
        "PATH",
        f"/home/alice/bin:/opt/node/bin:/usr/local/bin:/usr/bin:{project}/.venv/bin:/tmp/tools",
    )

    argv, child_env = build_bubblewrap_launch(
        "/usr/bin/bwrap",
        project,
        "/usr/bin/python3",
        [str(project / "server.py")],
    )

    rendered = repr((argv, child_env))
    assert secret not in rendered
    assert "CHARTER_HOST_SECRET" not in rendered
    assert "--unshare-all" in argv
    assert "--unshare-user" in argv
    assert "--new-session" in argv
    assert "--die-with-parent" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert ["--ro-bind", str(project.resolve()), "/workspace"] == argv[
        argv.index(str(project.resolve())) - 1 : argv.index(str(project.resolve())) + 2
    ]
    assert "--share-net" not in argv
    assert "--clearenv" in argv
    assert child_env["HOME"] == "/home/charter"
    assert "/home/alice/bin" not in child_env["PATH"]
    assert "/tmp/tools" not in child_env["PATH"]
    assert "/workspace/.venv/bin" in child_env["PATH"]
    assert argv[-2:] == ["/usr/bin/python3", "/workspace/server.py"]
