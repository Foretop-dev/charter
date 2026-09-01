"""A curated registry of published MCP server packages and the capabilities they expose.

R18. Charter's capability engine only ever ran on `Tool` objects, and `Tool` objects only come
from a live `tools/list` call — which needs `--enumerate`, a stdio transport, Linux and
Bubblewrap (`sandbox.require_bubblewrap`). So a default scan, and every scan on a non-Linux
machine, collapsed every declared server into one identical `review · not enumerated` row.
Reproduced live against the real chanzuckerberg/single-cell-data-portal: six servers, six
identical rows, nothing learned by a reader.

A published package's capabilities are knowable without launching it, in exactly the way ebb
already treats a model's retirement date as knowable without calling the provider: by
transcribing the vendor's own documentation and citing it. This module is that registry, and it
deliberately mirrors `ebb/registry/loader.py` — required `source_url` and `verified_at` on every
entry, globally unique keys, and a loud failure rather than a silent skip when either is
missing.

What this is not: a substitute for enumeration. A registry hit says what the *package* is
documented to do, never what the running server actually returned from `tools/list`. Callers must
keep the two apart — see `findings.py`, which reports a registry-derived capability as its own
finding with the registry's own source as evidence, and leaves the tool inventory itself
`unknown`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from charter.capability import CapabilityClass
from charter.models import Server

REGISTRY_DIR = Path(__file__).resolve().parent / "registries" / "servers"

# npx and friends take flags before the package name. Anything starting with "-" is a flag, and
# the first remaining token is the package being run.
_LAUNCHER_COMMANDS = frozenset({"npx", "bunx", "pnpx", "pnpm", "yarn", "uvx", "uv", "npm"})
# Subcommands a launcher may take before the package (e.g. `pnpm dlx <pkg>`, `npm exec <pkg>`).
_LAUNCHER_SUBCOMMANDS = frozenset({"dlx", "exec", "run", "tool"})


class ServerRegistryLoadError(Exception):
    """A bundled registry file is malformed, uncited, or declares a package twice."""


@dataclass(frozen=True, slots=True)
class ServerRegistryEntry:
    package: str
    capabilities: frozenset[CapabilityClass]
    source_url: str
    verified_at: date


def load_server_registry(paths: Iterable[Path]) -> dict[str, ServerRegistryEntry]:
    """Every curated package, keyed by package name.

    Refuses rather than degrades: a missing `source_url` or `verified_at`, an unrecognised
    capability class, or the same package declared twice all raise. An uncited capability claim
    is the one thing this registry must never ship, and a duplicate makes which entry wins
    depend on file ordering.
    """
    registry: dict[str, ServerRegistryEntry] = {}
    for path in paths:
        try:
            records = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            raise ServerRegistryLoadError(f"{path}: not valid YAML ({type(exc).__name__})") from exc
        if not isinstance(records, list):
            raise ServerRegistryLoadError(f"{path}: expected a list of entries")

        for record in records:
            if not isinstance(record, dict):
                raise ServerRegistryLoadError(f"{path}: expected a mapping per entry")
            package = record.get("package")
            source_url = record.get("source_url")
            verified_at = record.get("verified_at")
            raw_capabilities = record.get("capabilities")
            if not package:
                raise ServerRegistryLoadError(f"{path}: an entry has no package")
            if not source_url:
                raise ServerRegistryLoadError(f"{path}: {package} has no source_url")
            if not isinstance(verified_at, date):
                raise ServerRegistryLoadError(f"{path}: {package} has no valid verified_at")
            if not isinstance(raw_capabilities, list) or not raw_capabilities:
                raise ServerRegistryLoadError(f"{path}: {package} declares no capabilities")
            try:
                capabilities = frozenset(CapabilityClass(value) for value in raw_capabilities)
            except ValueError as exc:
                raise ServerRegistryLoadError(f"{path}: {package}: {exc}") from exc
            if package in registry:
                raise ServerRegistryLoadError(f"{package} is declared more than once")
            registry[package] = ServerRegistryEntry(
                package=package,
                capabilities=capabilities,
                source_url=source_url,
                verified_at=verified_at,
            )
    return registry


def load_bundled_server_registry() -> dict[str, ServerRegistryEntry]:
    return load_server_registry(sorted(REGISTRY_DIR.glob("*.yaml")))


def _launched_package(server: Server) -> str | None:
    """The package a stdio server's launch vector runs, or None.

    Only reads `command`/`args`, which the parser already captured — no new data is collected,
    and nothing here is echoed into output (DEC-06): callers receive a registry entry, never the
    argument text this inspected.
    """
    if server.command is None:
        return None
    command = Path(server.command).name
    # A launcher runs a package named in its args; anything else is a server invoked directly by
    # its own binary name (`server-github --flag`).
    candidates = list(server.args) if command in _LAUNCHER_COMMANDS else [command]

    for token in candidates:
        if token.startswith("-"):
            continue
        if token in _LAUNCHER_SUBCOMMANDS:
            continue
        return token
    return None


def _without_version(package: str) -> str:
    """`@playwright/mcp@latest` -> `@playwright/mcp`; `pkg@1.2.3` -> `pkg`.

    A scoped package's leading `@` is part of its name, so only a later `@` separates a version.
    """
    at = package.rfind("@")
    if at > 0:
        return package[:at]
    return package


def lookup_server_package(
    server: Server, registry: dict[str, ServerRegistryEntry]
) -> ServerRegistryEntry | None:
    """The curated entry for this server's launch vector, or None when it isn't curated.

    None is a real answer, not a failure: it is what keeps every capability charter reports
    traceable to a cited source.
    """
    package = _launched_package(server)
    if package is None:
        return None
    return registry.get(package) or registry.get(_without_version(package))
