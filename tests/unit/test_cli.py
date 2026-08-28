import json
import subprocess
import sys
from pathlib import Path

import pytest
from keel.report import ReportError
from typer.testing import CliRunner

import charter.enumerate as enumerate_module
from charter.cli import app

runner = CliRunner()

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "toy_mcp_server.py").resolve()


@pytest.fixture(autouse=True)
def protocol_tests_use_a_direct_test_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI protocol tests stay portable; the Linux integration suite proves isolation."""
    monkeypatch.setattr(enumerate_module, "require_bubblewrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(
        enumerate_module,
        "build_bubblewrap_launch",
        lambda _bwrap, _root, command, args: ([command, *args], {}),
    )


def make_service(tmp_path: Path) -> Path:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"airtable": {"command": "npx", "args": ["-y", "airtable-mcp-server"], '
        '"env": {"AIRTABLE_API_KEY": "pat_secret_value_should_never_appear"}}}}'
    )
    return tmp_path


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def make_git_service(tmp_path: Path) -> tuple[Path, str]:
    """A real git repo, one commit in, with a single server declared and its charter.lock
    already committed (--base needs a *committed* charter.lock — DEC-04's "the lock file is
    the review mechanism"). Returns (repo, its own base branch name)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test")

    (repo / ".mcp.json").write_text('{"mcpServers": {"svc1": {"command": "npx"}}}')
    result = runner.invoke(app, ["scan", str(repo)])
    assert result.exit_code == 0

    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "base: one server")
    base_branch = run_git(repo, "branch", "--show-current")
    return repo, base_branch


def make_enumerable_service(tmp_path: Path) -> Path:
    # A real, runnable server (the same toy_mcp_server.py test_enumerate.py's own subprocess
    # tests use), so --enumerate here launches something real rather than a made-up command
    # that would only ever exercise the "failed to launch" path.
    config = {"mcpServers": {"toy": {"command": sys.executable, "args": [str(FIXTURE)]}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(config))
    return tmp_path


def test_scan_exits_zero_and_prints_the_server(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service)])

    assert result.exit_code == 0
    assert "airtable" in result.stdout
    assert "AIRTABLE_API_KEY" in result.stdout


def test_scan_writes_charter_lock_by_default(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    runner.invoke(app, ["scan", str(service)])

    assert (service / "charter.lock").is_file()


def test_scan_respects_an_explicit_lock_path(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    custom_lock = tmp_path / "custom.lock"

    result = runner.invoke(app, ["scan", str(service), "--lock", str(custom_lock)])

    assert result.exit_code == 0
    assert custom_lock.is_file()
    assert not (service / "charter.lock").is_file()


def test_no_secret_value_ever_appears_in_stdout_or_the_lock_file(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service)])

    assert "pat_secret_value_should_never_appear" not in result.stdout
    lock_content = (service / "charter.lock").read_text()
    assert "pat_secret_value_should_never_appear" not in lock_content


def test_scan_default_path_is_the_current_directory(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    make_service(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["scan"])

    assert result.exit_code == 0
    assert "airtable" in result.stdout


def test_scan_an_empty_directory_exits_zero_with_zero_servers(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(tmp_path)])

    assert result.exit_code == 0
    assert "0 server(s)" in result.stdout


def test_scan_without_enumerate_never_shows_tool_names(tmp_path: Path) -> None:
    service = make_enumerable_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service)])

    assert result.exit_code == 0
    assert "read_file" not in result.stdout
    lock_content = (service / "charter.lock").read_text()
    assert '"tools": null' in lock_content


def test_scan_with_enumerate_launches_the_real_server_and_shows_its_tools(tmp_path: Path) -> None:
    service = make_enumerable_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service), "--enumerate", "--timeout", "5"])

    assert result.exit_code == 0
    assert "read_file" in result.stdout
    assert "write_file" in result.stdout
    lock_content = (service / "charter.lock").read_text()
    assert '"read_file"' in lock_content
    assert '"schema_version": 4' in lock_content
    assert '"capabilities"' in lock_content
    assert '"severity": "high"' in lock_content  # write_file -> write -> HIGH


def test_scan_format_markdown_renders_a_capability_table(tmp_path: Path) -> None:
    service = make_enumerable_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service), "--enumerate", "--format", "markdown"])

    assert result.exit_code == 0
    assert "### charter" in result.stdout
    assert "read_file" in result.stdout
    assert "write_file" in result.stdout
    # charter.lock is still written regardless of --format.
    assert (service / "charter.lock").is_file()


def test_scan_format_sarif_produces_valid_json(tmp_path: Path) -> None:
    service = make_enumerable_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service), "--enumerate", "--format", "sarif"])

    assert result.exit_code == 0
    log = json.loads(result.stdout)
    assert log["version"] == "2.1.0"
    rule_ids = {r["ruleId"] for r in log["runs"][0]["results"]}
    assert "write" in rule_ids


def test_scan_format_sarif_without_enumerate_still_exits_zero(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service), "--format", "sarif"])

    assert result.exit_code == 0
    log = json.loads(result.stdout)
    assert log["runs"][0]["results"] == []


def test_scan_an_unknown_format_exits_with_a_usable_error(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service), "--format", "bogus"])

    assert result.exit_code == 2
    assert "bogus" in result.output


def test_no_secret_value_ever_appears_in_markdown_or_sarif_output(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    markdown_result = runner.invoke(app, ["scan", str(service), "--format", "markdown"])
    sarif_result = runner.invoke(app, ["scan", str(service), "--format", "sarif"])

    assert "pat_secret_value_should_never_appear" not in markdown_result.stdout
    assert "pat_secret_value_should_never_appear" not in sarif_result.stdout


def test_scan_with_enumerate_on_a_failing_server_still_exits_zero(tmp_path: Path) -> None:
    config = {
        "mcpServers": {
            "toy": {"command": sys.executable, "args": [str(FIXTURE), "--crash-on-init"]}
        }
    }
    (tmp_path / ".mcp.json").write_text(json.dumps(config))

    result = runner.invoke(app, ["scan", str(tmp_path), "--enumerate", "--timeout", "5"])

    # An enumeration failure is recorded as evidence (enumeration_error), never a scan failure
    # by itself — no --base here, so the drift gate (Session 17) never even runs.
    assert result.exit_code == 0
    assert "error" in result.stdout.lower()


def test_scan_with_base_exits_1_on_a_new_server(tmp_path: Path) -> None:
    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch])

    assert result.exit_code == 1
    assert "svc2" in result.stdout


def test_scan_with_base_exits_0_when_nothing_drifted(tmp_path: Path) -> None:
    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / "README.md").write_text("unrelated change\n")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "unrelated")

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch])

    assert result.exit_code == 0


def test_scan_with_base_exits_2_on_an_unknown_ref(tmp_path: Path) -> None:
    repo, _base_branch = make_git_service(tmp_path)

    result = runner.invoke(app, ["scan", str(repo), "--base", "totally-not-a-real-ref"])

    assert result.exit_code == 2


def test_scan_format_annotations_shows_drift(tmp_path: Path) -> None:
    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )

    result = runner.invoke(
        app, ["scan", str(repo), "--base", base_branch, "--format", "annotations"]
    )

    assert result.exit_code == 1
    assert "::error file=.mcp.json" in result.stdout
    assert "svc2" in result.stdout


def test_scan_format_annotations_is_empty_without_drift(tmp_path: Path) -> None:
    repo, base_branch = make_git_service(tmp_path)

    result = runner.invoke(
        app, ["scan", str(repo), "--base", base_branch, "--format", "annotations"]
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_scan_format_markdown_includes_drift_section_when_base_is_given(tmp_path: Path) -> None:
    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch, "--format", "markdown"])

    assert result.exit_code == 1
    assert "Drift since the merge base" in result.stdout


def test_scan_without_base_never_exits_1(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = runner.invoke(app, ["scan", str(service)])

    assert result.exit_code == 0


def test_gate_defaults_to_off_and_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )

    def fail_if_called(**kwargs: object) -> object:
        raise AssertionError("maybe_fetch_gate should not be called when --gate is absent")

    monkeypatch.setattr("charter.cli.maybe_fetch_gate", fail_if_called)

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch])

    assert result.exit_code == 1


def test_gate_without_base_is_a_noop_and_makes_no_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path)

    def fail_if_called(**kwargs: object) -> object:
        raise AssertionError("maybe_fetch_gate should not be called without --base")

    monkeypatch.setattr("charter.cli.maybe_fetch_gate", fail_if_called)

    result = runner.invoke(app, ["scan", str(service), "--gate"])

    assert result.exit_code == 0


def test_gate_excludes_a_baselined_new_server_from_the_exit_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from keel.gate import Baseline, GateResponse

    from charter.findings import compute_identity

    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )
    identity = compute_identity("svc2", None, None)
    monkeypatch.setattr(
        "charter.cli.maybe_fetch_gate",
        lambda **kwargs: GateResponse(baseline=Baseline(name="main", identities=[identity])),
    )

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch, "--gate"])

    assert result.exit_code == 0
    assert "gate: 1 finding(s) excluded (1 baselined)" in result.stdout


def test_gate_fetch_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from keel.gate import GateFetchError

    repo, base_branch = make_git_service(tmp_path)
    run_git(repo, "checkout", "-q", "-b", "feature")
    (repo / ".mcp.json").write_text(
        '{"mcpServers": {"svc1": {"command": "npx"}, "svc2": {"command": "npx"}}}'
    )

    def raise_gate_error(**kwargs: object) -> None:
        raise GateFetchError("FORETOP_TOKEN is not set")

    monkeypatch.setattr("charter.cli.maybe_fetch_gate", raise_gate_error)

    result = runner.invoke(app, ["scan", str(repo), "--base", base_branch, "--gate"])

    assert result.exit_code == 2


def test_report_defaults_to_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path)
    calls = []
    monkeypatch.setattr("charter.cli.maybe_report", lambda **kwargs: calls.append(kwargs))

    result = runner.invoke(app, ["scan", str(service)])

    assert result.exit_code == 0
    assert calls == []


def test_report_flag_calls_maybe_report_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path)
    calls = []
    monkeypatch.setattr("charter.cli.maybe_report", lambda **kwargs: calls.append(kwargs))

    result = runner.invoke(app, ["scan", str(service), "--report"])

    assert result.exit_code == 0
    assert calls[0]["enabled"] is True
    assert calls[0]["product"] == "charter"


def test_report_alone_attaches_a_real_triage_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = make_service(tmp_path)
    calls = []
    monkeypatch.setattr("charter.cli.maybe_report", lambda **kwargs: calls.append(kwargs))

    result = runner.invoke(app, ["scan", str(service), "--report"])

    assert result.exit_code == 0
    assert calls[0]["triage"] is not None


def test_report_and_triage_json_format_together_compute_triage_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import charter.cli as charter_cli

    service = make_service(tmp_path)
    calls = []
    monkeypatch.setattr("charter.cli.maybe_report", lambda **kwargs: calls.append(kwargs))
    real_build_triage = charter_cli.build_triage
    call_count = {"n": 0}

    def spy(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return real_build_triage(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("charter.cli.build_triage", spy)

    result = runner.invoke(app, ["scan", str(service), "--format", "triage-json", "--report"])

    assert result.exit_code == 0
    assert call_count["n"] == 1
    assert calls[0]["triage"] is not None


def test_plain_scan_never_computes_triage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path)

    def fail_if_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("build_triage should not be called for a plain --format table scan")

    monkeypatch.setattr("charter.cli.build_triage", fail_if_called)

    result = runner.invoke(app, ["scan", str(service)])

    assert result.exit_code == 0


def test_report_error_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = make_service(tmp_path)

    def raise_report_error(**kwargs: object) -> None:
        raise ReportError("FORETOP_TOKEN is not set")

    monkeypatch.setattr("charter.cli.maybe_report", raise_report_error)

    result = runner.invoke(app, ["scan", str(service), "--report"])

    assert result.exit_code == 2
