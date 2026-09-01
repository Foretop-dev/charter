"""R18: charter could say nothing about a server it had not launched.

Capabilities only ever existed for `Tool` objects, and `Tool` objects only come from a live
`tools/list` call — which needs `--enumerate`, stdio transport, Linux and Bubblewrap. A default
scan, and every scan on macOS, therefore collapsed every server into one identical
`review · not enumerated` row. Reproduced live against the real
chanzuckerberg/single-cell-data-portal: six declared servers, six identical rows, nothing
learned.

A published MCP server package's capabilities are knowable without launching it, the same way a
model's retirement date is knowable without calling the provider — by curating the vendor's own
documentation and citing it. This mirrors ebb's registry exactly, including its discipline: an
entry carries the source it came from and the date a person checked, and a package with no entry
stays unknown rather than being guessed at from its name.
"""

from datetime import date
from pathlib import Path

import pytest

from charter.capability import CapabilityClass
from charter.models import Server, Transport
from charter.server_registry import (
    ServerRegistryLoadError,
    load_server_registry,
    lookup_server_package,
)

REGISTRY_DIR = Path(__file__).resolve().parents[2] / "src" / "charter" / "registries" / "servers"


def _server(command: str | None, args: tuple[str, ...], **kwargs: object) -> Server:
    defaults: dict[str, object] = {
        "name": "s",
        "transport": Transport.STDIO,
        "command": command,
        "args": args,
        "env_var_names": (),
        "url": None,
        "header_names": (),
        "source_file": Path(".mcp.json"),
        "source_line": 1,
    }
    defaults.update(kwargs)
    return Server(**defaults)  # type: ignore[arg-type]


def test_the_bundled_registry_loads_and_every_entry_cites_a_source() -> None:
    """Same hard requirement ebb's registry carries: a capability claim with no source and no
    verification date is an assertion, not evidence."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))

    assert registry
    for package, entry in registry.items():
        assert entry.package == package
        assert entry.source_url.startswith("https://"), package
        assert isinstance(entry.verified_at, date), package
        assert entry.capabilities, f"{package} claims no capabilities at all"


def test_a_real_npx_launch_vector_resolves_to_its_package() -> None:
    """The exact shapes committed in real repositories: `npx --yes <pkg> <arg>` and
    `npx -y <pkg>`, both taken verbatim from chanzuckerberg/single-cell-data-portal."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))

    filesystem = _server("npx", ("--yes", "@modelcontextprotocol/server-filesystem", "."))
    entry = lookup_server_package(filesystem, registry)
    assert entry is not None
    assert entry.package == "@modelcontextprotocol/server-filesystem"
    assert CapabilityClass.WRITE in entry.capabilities

    playwright = _server("npx", ("-y", "@playwright/mcp@latest"))
    entry = lookup_server_package(playwright, registry)
    assert entry is not None, "a version suffix must not defeat the lookup"
    assert CapabilityClass.CODE_EXECUTION in entry.capabilities


def test_an_uncurated_package_stays_unknown_rather_than_being_guessed() -> None:
    """The invariant that makes the rest trustworthy. `@czi-sds/mcp` and
    `@zeroheight/mcp-server` are both real, both committed in that same real config, and
    neither is curated — so charter must keep saying it does not know."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))

    for package in ("@czi-sds/mcp", "@zeroheight/mcp-server@latest", "@some/never-seen"):
        assert lookup_server_package(_server("npx", ("-y", package)), registry) is None


def test_a_server_with_no_launch_vector_resolves_to_nothing() -> None:
    """An HTTP server has no command at all — it must not accidentally match on a URL."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))
    http = _server(
        None, (), transport=Transport.HTTP, url="https://mcp.example.invalid/server-github"
    )

    assert lookup_server_package(http, registry) is None


def test_an_entry_missing_its_source_is_refused(tmp_path: Path) -> None:
    """Loud at load time rather than silently shipping an uncited claim."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        "- package: '@example/thing'\n  capabilities: [read]\n  verified_at: 2026-09-01\n",
        encoding="utf-8",
    )

    with pytest.raises(ServerRegistryLoadError):
        load_server_registry([path])


def test_an_unrecognised_capability_class_is_refused(tmp_path: Path) -> None:
    """CapabilityClass is exactly the five specs/charter.md §2.2 names; a typo or an invented
    sixth must fail the load, not silently become an unknown severity."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        "- package: '@example/thing'\n"
        "  capabilities: [telepathy]\n"
        "  source_url: https://example.invalid/docs\n"
        "  verified_at: 2026-09-01\n",
        encoding="utf-8",
    )

    with pytest.raises(ServerRegistryLoadError):
        load_server_registry([path])


def test_a_package_declared_twice_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "dupe.yaml"
    entry = (
        "- package: '@example/thing'\n"
        "  capabilities: [read]\n"
        "  source_url: https://example.invalid/docs\n"
        "  verified_at: 2026-09-01\n"
    )
    path.write_text(entry + entry, encoding="utf-8")

    with pytest.raises(ServerRegistryLoadError):
        load_server_registry([path])


def test_the_dev_tooling_packages_seen_in_real_configs_are_curated() -> None:
    """R22: every package below was found in a real committed `.vscode/mcp.json` or
    `.mcp.json`, and each capability is transcribed from that project's own documentation.

    `@angular/cli` and `chrome-devtools-mcp` both execute code — `run_target` "executes a
    configured target (build, test, lint, e2e, deploy)" and `evaluate_script` evaluates
    JavaScript in the browser — which is exactly the kind of reach a reviewer should see."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))

    for package in ("chrome-devtools-mcp", "@angular/cli"):
        entry = registry[package]
        assert CapabilityClass.CODE_EXECUTION in entry.capabilities, package

    # nx-mcp describes generators, it does not invoke one — the distinction between it and
    # @angular/cli's run_target, and the reason it is not marked code_execution.
    assert registry["nx-mcp"].capabilities == frozenset({CapabilityClass.READ})


def test_a_versioned_launch_vector_for_a_dev_tool_still_resolves() -> None:
    """Real configs pin these with `@latest` (`chrome-devtools-mcp@latest`, `nx-mcp@latest`),
    and `@angular/cli` is invoked with its subcommand as a following argument."""
    registry = load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))

    cases = {
        ("npx", ("chrome-devtools-mcp@latest",)): "chrome-devtools-mcp",
        ("npx", ("-y", "nx-mcp@latest")): "nx-mcp",
        ("npx", ("@angular/cli", "mcp")): "@angular/cli",
    }
    for (command, args), expected in cases.items():
        entry = lookup_server_package(_server(command, args), registry)
        assert entry is not None, f"{command} {args} did not resolve"
        assert entry.package == expected
