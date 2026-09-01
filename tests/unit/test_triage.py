from pathlib import Path

from keel.triage import Lane

from charter.drift import Drift, DriftKind
from charter.enumerate import EnumerationResult, Tool
from charter.models import Server, ServerSet, Transport
from charter.triage import build_triage

ROOT = Path("/repo")


def make_server(**overrides: object) -> Server:
    defaults: dict[str, object] = {
        "name": "svc",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("-y", "server"),
        "env_var_names": ("API_KEY",),
        "url": None,
        "header_names": (),
        "source_file": ROOT / ".mcp.json",
        "source_line": 3,
    }
    defaults.update(overrides)
    return Server(**defaults)  # type: ignore[arg-type]


def make_tool(**overrides: object) -> Tool:
    defaults: dict[str, object] = {
        "server_name": "svc",
        "name": "read_file",
        "description": "Read a file from disk",
        "input_schema": None,
    }
    defaults.update(overrides)
    return Tool(**defaults)  # type: ignore[arg-type]


def make_drift(
    server_name: str = "svc",
    tool_name: str | None = None,
    kind: DriftKind = DriftKind.NEW_SERVER,
) -> Drift:
    return Drift(
        server_name=server_name,
        source_file=".mcp.json",
        source_line=3,
        tool_name=tool_name,
        kind=kind,
        before_severity=None,
        after_severity=None,
    )


def test_a_never_enumerated_server_is_one_review_group_not_multiple_alarms() -> None:
    """An HTTP/SSE/WS server is never contacted (enumerate.py's own stdio-only guard) — this
    must land as exactly one group, not one per undeclared capability.

    `env_var_names` is cleared because R18 made a declared credential its own reason to plan,
    and this fixture's default `API_KEY` would otherwise move the group out of the review lane
    this test exists to exercise. The property under test is the grouping, not the lane."""
    server = make_server(
        transport=Transport.HTTP,
        command=None,
        url="https://example.com/mcp",
        env_var_names=(),
    )
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {}, (), ROOT)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.lane is Lane.REVIEW
    assert "not enumerated" in group.reason
    assert group.occurrence_count == 1


def test_an_enumeration_error_is_also_review() -> None:
    server = make_server()
    result = EnumerationResult(server_name=server.name, tools=(), error="connection refused")
    server_set = ServerSet(servers=(server,))

    triage = build_triage(server_set, {server: result}, (), ROOT)

    assert triage.groups[0].lane is Lane.REVIEW
    assert "connection refused" in triage.groups[0].reason


def test_a_new_server_since_baseline_is_act_now() -> None:
    server = make_server()
    server_set = ServerSet(servers=(server,))
    drift = (make_drift(server_name=server.name, kind=DriftKind.NEW_SERVER),)

    result = build_triage(server_set, {}, drift, ROOT)

    assert result.groups[0].lane is Lane.ACT_NOW
    assert "new server" in result.groups[0].reason


def test_a_drifted_tool_is_act_now() -> None:
    server = make_server()
    tool = make_tool(name="run_shell", description="Execute a shell command")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))
    drift = (make_drift(server_name=server.name, tool_name=tool.name, kind=DriftKind.NEW_TOOL),)

    result = build_triage(server_set, {server: enum_result}, drift, ROOT)

    assert result.groups[0].lane is Lane.ACT_NOW
    assert "changed since baseline" in result.groups[0].reason


def test_an_enumerated_server_with_a_high_risk_capability_is_plan() -> None:
    server = make_server()
    tool = make_tool(name="run_shell", description="Execute an arbitrary shell command")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    group = result.groups[0]
    assert group.lane is Lane.PLAN
    assert "code_execution" in group.reason


def test_a_clean_enumerated_server_is_inventory() -> None:
    server = make_server()
    tool = make_tool(name="read_file", description="Read a file from disk")
    enum_result = EnumerationResult(server_name=server.name, tools=(tool,), error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    assert result.groups[0].lane is Lane.INVENTORY


def test_multiple_tools_and_capabilities_all_fold_into_one_group() -> None:
    server = make_server()
    tools = (
        make_tool(name="read_file", description="Read a file from disk"),
        make_tool(name="write_file", description="Write or delete a file on disk"),
        make_tool(name="run_shell", description="Execute an arbitrary shell command"),
    )
    enum_result = EnumerationResult(server_name=server.name, tools=tools, error=None)
    server_set = ServerSet(servers=(server,))

    result = build_triage(server_set, {server: enum_result}, (), ROOT)

    assert len(result.groups) == 1
    group = result.groups[0]
    # 1 server-presence occurrence + 3 tools x >=1 capability each.
    assert group.occurrence_count >= 4
    assert group.context_count >= 3


def test_observations_sums_every_occurrence_across_all_groups() -> None:
    server_a = make_server(name="svc-a")
    server_b = make_server(name="svc-b", source_file=ROOT / ".cursor" / "mcp.json")
    server_set = ServerSet(servers=(server_a, server_b))

    result = build_triage(server_set, {}, (), ROOT)

    assert result.issue_groups == 2
    assert result.observations == 2  # one server-presence occurrence each, neither enumerated


# ---------------------------------------------------------------- static signal (R18)


def test_a_documented_high_risk_package_is_planned_not_merely_reviewed() -> None:
    """R18: a server charter has not launched used to be indistinguishable from one it knows
    nothing about. `@playwright/mcp` documents `browser_run_code_unsafe`, which its own README
    calls unsafe because it "executes arbitrary JavaScript in the Playwright server process" —
    that is knowable from the published package without launching anything, and it is worth
    more than "not enumerated"."""
    server = make_server(name="playwright", args=("-y", "@playwright/mcp@latest"), env_var_names=())

    result = build_triage(ServerSet(servers=(server,)), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.PLAN
    assert "code_execution" in group.reason
    assert "documented" in group.reason, "the reason must not imply it was enumerated"
    assert group.attributes["capability_source"] == "registry"


def test_a_documented_package_with_nothing_elevated_is_inventory() -> None:
    """`@modelcontextprotocol/server-filesystem` documents read and write and nothing higher.
    Knowing that is a real answer — it belongs in inventory, not in the review pile."""
    server = make_server(
        name="filesystem",
        args=("--yes", "@modelcontextprotocol/server-filesystem", "."),
        env_var_names=(),
    )

    result = build_triage(ServerSet(servers=(server,)), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.INVENTORY
    assert "read, write" in group.reason


def test_a_declared_credential_env_var_is_planned_even_without_a_registry_entry() -> None:
    """The config itself declares that this server is wired to a credential. That is a static
    fact charter already parsed (Server.env_var_names) and never used. Taken from the real
    chanzuckerberg config's `zeroheight` entry, whose package is deliberately not curated."""
    server = make_server(
        name="zeroheight",
        args=("-y", "@zeroheight/mcp-server@latest"),
        env_var_names=("ZEROHEIGHT_ACCESS_TOKEN", "ZEROHEIGHT_CLIENT_ID"),
    )

    result = build_triage(ServerSet(servers=(server,)), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.PLAN
    assert "ZEROHEIGHT_ACCESS_TOKEN" in group.reason


def test_a_credential_bearing_argument_is_reported_without_echoing_it() -> None:
    """The sharpest shape found in a real committed config: a postgres server launched with a
    DSN carrying inline userinfo. Charter must say a credential is embedded in an argument and
    must never reproduce the argument (DEC-06).

    The credential here is synthetic, matching test_acceptance.py's own precedent — the real
    repository this shape came from is public, but copying someone else's committed credential
    into this one buys nothing the shape does not already prove."""
    server = make_server(
        name="postgresql",
        args=(
            "--yes",
            "@modelcontextprotocol/server-postgres",
            "postgresql://svc:argument-password-4c7e@localhost:5432/appdb",
        ),
        env_var_names=(),
    )

    result = build_triage(ServerSet(servers=(server,)), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.PLAN
    assert "credential" in group.reason.lower()
    serialized = repr(result)
    assert "argument-password-4c7e" not in serialized
    assert "appdb" not in serialized


def test_an_uncurated_server_with_no_credential_keeps_saying_it_does_not_know() -> None:
    """The invariant. `@czi-sds/mcp` is real, committed, and not curated — the honest answer is
    still that static parsing cannot say what it exposes."""
    server = make_server(name="sds-mcp", args=("-y", "@czi-sds/mcp"), env_var_names=())

    result = build_triage(ServerSet(servers=(server,)), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.REVIEW
    assert "not enumerated" in group.reason


def test_a_real_enumeration_still_wins_over_the_registry() -> None:
    """The registry says what a published package is documented to do; enumeration says what
    this server actually returned. When both exist the real answer must win, and the reason
    must not claim the weaker source."""
    server = make_server(
        name="filesystem", args=("--yes", "@modelcontextprotocol/server-filesystem", ".")
    )
    enumeration = {
        server: EnumerationResult(
            server_name="filesystem", tools=(make_tool(name="read_file"),), error=None
        )
    }

    result = build_triage(ServerSet(servers=(server,)), enumeration, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.INVENTORY
    assert "enumerated, 1 tool(s)" in group.reason
    assert group.attributes["capability_source"] == "enumeration"


# ------------------------------------------------- one server, several config files (R22)


def test_the_same_server_declared_in_several_configs_is_one_group() -> None:
    """R22: adding `.vscode/mcp.json` made a latent bug common. A team using more than one
    client declares the same server in each client's config — the real dotCMS/core repository
    declares `angular-cli` identically in `.mcp.json`, `.cursor/mcp.json` and
    `.vscode/mcp.json`, and charter emitted three groups carrying the *same* fingerprint.

    That is not merely noisy: `hub.models.IssueGroupRow` is unique on
    `(run_id, fingerprint)`, so ingesting such a run would violate its own constraint. One
    server declared three times is one server, and each declaration is an occurrence — the
    same folding triage.py already does for a server's tools."""
    common = {
        "name": "angular-cli",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("@angular/cli", "mcp"),
        "env_var_names": (),
    }
    servers = tuple(
        make_server(**common, source_file=ROOT / f, source_line=n)
        for f, n in ((".mcp.json", 13), (".cursor/mcp.json", 10), (".vscode/mcp.json", 13))
    )

    result = build_triage(ServerSet(servers=servers), {}, (), ROOT)

    assert len(result.groups) == 1, "one server declared three times is one group"
    (group,) = result.groups
    assert group.label == "angular-cli"
    assert group.occurrence_count == 3, "each declaration is its own occurrence"
    sources = {o["source_uri"] if isinstance(o, dict) else o.source_uri for o in group.occurrences}
    assert sources == {".mcp.json", ".cursor/mcp.json", ".vscode/mcp.json"}


def test_every_group_in_a_run_has_a_distinct_fingerprint() -> None:
    """The property the hub constraint actually depends on, asserted directly rather than
    inferred from the group count."""
    servers = tuple(
        make_server(name=name, source_file=ROOT / f, source_line=1)
        for name, f in (
            ("angular-cli", ".mcp.json"),
            ("angular-cli", ".vscode/mcp.json"),
            ("nx-mcp", ".cursor/mcp.json"),
            ("nx-mcp", ".vscode/mcp.json"),
            ("primeng", ".mcp.json"),
        )
    )

    result = build_triage(ServerSet(servers=servers), {}, (), ROOT)

    fingerprints = [g.fingerprint for g in result.groups]
    assert len(fingerprints) == len(set(fingerprints)), "duplicate fingerprints in one run"
    assert {g.label for g in result.groups} == {"angular-cli", "nx-mcp", "primeng"}


def test_folding_keeps_the_most_serious_lane_across_declarations() -> None:
    """If one declaration wires a credential and another does not, the reviewer needs the
    credential-bearing answer — taking whichever happened to be parsed first would hide it."""
    servers = (
        make_server(name="gh", source_file=ROOT / ".mcp.json", env_var_names=()),
        make_server(
            name="gh",
            source_file=ROOT / ".vscode/mcp.json",
            env_var_names=("GITHUB_PERSONAL_ACCESS_TOKEN",),
        ),
    )

    result = build_triage(ServerSet(servers=servers), {}, (), ROOT)

    (group,) = result.groups
    assert group.lane is Lane.PLAN
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in group.reason
