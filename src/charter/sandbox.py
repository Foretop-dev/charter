"""Fail-closed Linux isolation for live MCP server enumeration.

Bubblewrap is deliberately a system dependency rather than a Python package: it creates the
kernel namespaces and mount policy before the configured server starts. The caller supplies
the policy, so every mount and environment entry stays explicit here.
"""

import os
import platform
import shutil
import subprocess
from functools import cache
from pathlib import Path

_WORKSPACE = Path("/workspace")
_SANDBOX_HOME = "/home/charter"
_SYSTEM_PATH_ROOTS = tuple(Path(path) for path in ("/usr", "/bin", "/sbin", "/opt"))
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
_PROBE_TIMEOUT_SECONDS = 5.0


class SandboxUnavailableError(RuntimeError):
    """The required enumeration isolation boundary cannot be established."""


def _relative_to(path: Path, parent: Path) -> Path | None:
    try:
        return path.resolve().relative_to(parent.resolve())
    except ValueError:
        return None


def _sandbox_path(project_root: Path | None = None) -> str:
    """Keep only executable search paths that are actually mounted into the sandbox."""
    allowed: list[str] = []
    for raw_entry in os.environ.get("PATH", _FALLBACK_PATH).split(os.pathsep):
        if not raw_entry:
            continue
        entry = Path(raw_entry)
        if not entry.is_absolute():
            continue

        if project_root is not None:
            project_relative = _relative_to(entry, project_root)
            if project_relative is not None:
                mapped = str(_WORKSPACE / project_relative)
                if mapped not in allowed:
                    allowed.append(mapped)
                continue

        if (
            any(_relative_to(entry, root) is not None for root in _SYSTEM_PATH_ROOTS)
            and raw_entry not in allowed
        ):
            allowed.append(raw_entry)

    for fallback in _FALLBACK_PATH.split(":"):
        if fallback not in allowed:
            allowed.append(fallback)
    return ":".join(allowed)


def _sanitized_environment(project_root: Path | None = None) -> dict[str, str]:
    # Supplying this small environment to bwrap itself is intentional defence in depth: the
    # child also gets --clearenv, but no credential should reach the isolation process in the
    # first place.
    return {
        "HOME": _SANDBOX_HOME,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": _sandbox_path(project_root),
        "TMPDIR": "/tmp",
    }


def _probe_command(bubblewrap: str) -> list[str]:
    return [
        bubblewrap,
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--unshare-all",
        "--unshare-user",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "PATH",
        _FALLBACK_PATH,
        "--",
        "/bin/true",
    ]


@cache
def require_bubblewrap() -> str:
    """Return a verified Bubblewrap executable or reject enumeration globally."""
    if platform.system() != "Linux":
        raise SandboxUnavailableError(
            "--enumerate requires Linux and Bubblewrap; static scans remain available on "
            "this platform"
        )

    bubblewrap = shutil.which("bwrap")
    if bubblewrap is None:
        raise SandboxUnavailableError(
            "--enumerate requires Bubblewrap; install the 'bubblewrap' system package or "
            "run a static scan without --enumerate"
        )

    try:
        probe = subprocess.run(
            _probe_command(bubblewrap),
            capture_output=True,
            check=False,
            env=_sanitized_environment(),
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailableError(f"Bubblewrap isolation could not be verified: {exc}") from exc

    if probe.returncode != 0:
        detail = probe.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise SandboxUnavailableError(
            f"Bubblewrap is installed but cannot create the required isolation boundary{suffix}"
        )
    return bubblewrap


def _map_project_path(value: str, project_root: Path) -> str:
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    relative = _relative_to(candidate, project_root)
    return str(_WORKSPACE / relative) if relative is not None else value


def build_bubblewrap_launch(
    bubblewrap: str, project_root: Path, command: str, args: list[str]
) -> tuple[list[str], dict[str, str]]:
    """Construct the only permitted launch vector for a configured stdio server."""
    root = project_root.resolve()
    sandbox_path = _sandbox_path(root)
    launch = [
        bubblewrap,
        # Start from Bubblewrap's empty tmpfs root and expose only runtime files plus the
        # scanned repository. /opt supports hosted-runner toolchains such as Node, but is
        # read-only. Host homes, /run and arbitrary /var content are never mounted.
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/opt",
        "/opt",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--dir",
        "/etc",
        "--ro-bind-try",
        "/etc/alternatives",
        "/etc/alternatives",
        "--ro-bind-try",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--ro-bind-try",
        "/etc/ssl/certs",
        "/etc/ssl/certs",
        "--dir",
        "/tmp",
        "--dir",
        "/var",
        "--symlink",
        "../tmp",
        "/var/tmp",
        "--dir",
        "/home",
        "--dir",
        _SANDBOX_HOME,
        "--dir",
        "/run",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--ro-bind",
        str(root),
        str(_WORKSPACE),
        "--chdir",
        str(_WORKSPACE),
        # --unshare-all includes the network namespace. Omitting --share-net is the policy,
        # not an accident: enumeration learns local protocol metadata and gets no egress.
        "--unshare-all",
        "--unshare-user",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "HOME",
        _SANDBOX_HOME,
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PATH",
        sandbox_path,
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--",
        _map_project_path(command, root),
        *(_map_project_path(arg, root) for arg in args),
    ]
    return launch, _sanitized_environment(root)
