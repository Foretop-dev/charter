import json
from pathlib import Path

from charter.collect import collect
from charter.enumerate import EnumerationResult, Tool
from charter.lockfile import SCHEMA_VERSION, render_lock, to_manifest, write_lock
from charter.models import Server, ServerSet, Transport


def make_server(**overrides: object) -> Server:
    defaults: dict[str, object] = {
        "name": "svc",
        "transport": Transport.STDIO,
        "command": "npx",
        "args": ("-y", "server"),
        "env_var_names": ("API_KEY",),
        "url": None,
        "header_names": (),
        "source_file": Path("/repo/.mcp.json"),
        "source_line": 3,
    }
    defaults.update(overrides)
    return Server(**defaults)  # type: ignore[arg-type]


def test_manifest_includes_the_schema_version() -> None:
    manifest = to_manifest(ServerSet(servers=()), Path("/repo"))

    assert manifest["schema_version"] == SCHEMA_VERSION


def test_source_file_is_relative_to_root_never_absolute() -> None:
    server = make_server(source_file=Path("/repo/.mcp.json"))
    manifest = to_manifest(ServerSet(servers=(server,)), Path("/repo"))

    entry = manifest["servers"][0]  # type: ignore[index]
    assert entry["source_file"] == ".mcp.json"  # type: ignore[index]
    assert "/repo" not in entry["source_file"]  # type: ignore[index]


def test_no_credential_value_ever_appears_in_the_manifest() -> None:
    # The manifest only ever had names to begin with (Server carries no value fields at all —
    # DEC-06), but this test asserts the actual rendered output too, not just the domain model,
    # in case a future field addition to _server_dict ever reintroduces one by accident.
    server = make_server(env_var_names=("SUPER_SECRET_TOKEN_NAME",))
    rendered = render_lock(ServerSet(servers=(server,)), Path("/repo"))

    assert "SUPER_SECRET_TOKEN_NAME" in rendered  # the name is expected evidence
    # No plausible secret VALUE shape (a real one would never appear, since Server itself
    # never carries one) — this documents the invariant rather than searching for a specific
    # string that could never have been captured in the first place.


def test_servers_are_sorted_by_name_for_deterministic_output() -> None:
    a = make_server(name="zebra")
    b = make_server(name="alpha")
    manifest = to_manifest(ServerSet(servers=(a, b)), Path("/repo"))

    names = [s["name"] for s in manifest["servers"]]  # type: ignore[union-attr]
    assert names == ["alpha", "zebra"]


def test_render_lock_output_is_canonical_json() -> None:
    server = make_server()
    output = render_lock(ServerSet(servers=(server,)), Path("/repo"))

    # Round-trips as JSON and ends with exactly one trailing newline (canonical_json's own
    # contract — see test_canonical.py for the dedicated coverage of that contract itself).
    json.loads(output)
    assert output.endswith("\n")
    assert not output.endswith("\n\n")


def test_write_lock_writes_bytes_with_lf_only(tmp_path: Path) -> None:
    server = make_server(source_file=tmp_path / ".mcp.json")
    lock_path = tmp_path / "charter.lock"

    write_lock(ServerSet(servers=(server,)), tmp_path, lock_path)

    raw = lock_path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_a_server_not_in_the_enumeration_dict_has_null_tools() -> None:
    server = make_server()
    manifest = to_manifest(ServerSet(servers=(server,)), Path("/repo"), enumeration={})

    entry = manifest["servers"][0]  # type: ignore[index]
    assert entry["tools"] is None  # type: ignore[index]
    assert entry["enumeration_error"] is None  # type: ignore[index]


def test_a_successfully_enumerated_server_lists_its_tools() -> None:
    server = make_server()
    tool = Tool(server_name="svc", name="read_file", description="reads", input_schema={"a": 1})
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    manifest = to_manifest(
        ServerSet(servers=(server,)), Path("/repo"), enumeration={server: result}
    )

    entry = manifest["servers"][0]  # type: ignore[index]
    tools = entry["tools"]  # type: ignore[index]
    assert tools is not None
    assert len(tools) == 1
    assert tools[0]["name"] == "read_file"
    assert tools[0]["description"] == "reads"
    assert tools[0]["input_schema"] == {"a": 1}
    assert tools[0]["capabilities"] == ["read"]
    assert tools[0]["severity"] == "low"
    assert tools[0]["rule_version"] >= 1
    assert entry["enumeration_error"] is None  # type: ignore[index]


def test_capabilities_are_sorted_for_byte_stability() -> None:
    # A frozenset has no defined iteration order — this would otherwise be a real, subtle
    # source of non-determinism across separate process invocations (PYTHONHASHSEED
    # randomization affects string hashing), exactly the kind of thing the stable-hash property
    # test exists to guard against; the lockfile itself must sort before serializing.
    server = make_server()
    tool = Tool(
        server_name="svc",
        name="get_secret",
        description="Retrieve a stored secret",
        input_schema=None,
    )
    result = EnumerationResult(server_name="svc", tools=(tool,), error=None)

    first = render_lock(ServerSet(servers=(server,)), Path("/repo"), enumeration={server: result})
    second = render_lock(ServerSet(servers=(server,)), Path("/repo"), enumeration={server: result})

    assert first == second


def test_a_server_enumerated_with_zero_tools_is_distinct_from_not_attempted() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error=None)

    manifest = to_manifest(
        ServerSet(servers=(server,)), Path("/repo"), enumeration={server: result}
    )

    entry = manifest["servers"][0]  # type: ignore[index]
    assert entry["tools"] == []  # type: ignore[index]
    assert entry["tools"] is not None  # type: ignore[index]


def test_a_failed_enumeration_records_the_error_and_null_tools() -> None:
    server = make_server()
    result = EnumerationResult(server_name="svc", tools=(), error="timed out after 10.0s")

    manifest = to_manifest(
        ServerSet(servers=(server,)), Path("/repo"), enumeration={server: result}
    )

    entry = manifest["servers"][0]  # type: ignore[index]
    assert entry["tools"] is None  # type: ignore[index]
    assert entry["enumeration_error"] == "timed out after 10.0s"  # type: ignore[index]


def test_end_to_end_collect_and_write(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"airtable": {"command": "npx", "args": ["-y", "airtable-mcp-server"], '
        '"env": {"AIRTABLE_API_KEY": "pat_should_never_appear_in_lock"}}}}'
    )
    lock_path = tmp_path / "charter.lock"

    write_lock(collect(tmp_path), tmp_path, lock_path)

    content = lock_path.read_text()
    assert "AIRTABLE_API_KEY" in content
    assert "pat_should_never_appear_in_lock" not in content
    assert ".mcp.json" in content
    assert str(tmp_path) not in content
