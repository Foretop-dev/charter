"""Fail-closed Linux isolation for live MCP server enumeration.

Bubblewrap is deliberately a system dependency rather than a Python package: it creates the
kernel namespaces and mount policy before the configured server starts. The caller supplies
the policy, so every mount and environment entry stays explicit here.
"""

import os
import platform
import shutil
import struct
import subprocess
from errno import EPERM
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

_WORKSPACE = Path("/workspace")
_SANDBOX_HOME = "/home/charter"
_SYSTEM_PATH_ROOTS = tuple(Path(path) for path in ("/usr", "/bin", "/sbin", "/opt"))
_FALLBACK_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin"
_PROBE_TIMEOUT_SECONDS = 5.0

# Linux classic-BPF/seccomp constants. Bubblewrap accepts a raw sock_filter array through
# --seccomp; keeping this tiny program here avoids a Python/libseccomp dependency for one rule.
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_JMP_JGE_K = 0x35
_BPF_RET_K = 0x06
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_SECCOMP_DATA_NR_OFFSET = 0
_SECCOMP_DATA_ARCH_OFFSET = 4
_X32_SYSCALL_BIT = 0x40000000
_MFD_CLOEXEC = 0x0001
_SECCOMP_ARCHES = {
    "aarch64": (0xC00000B7, 198),
    "arm64": (0xC00000B7, 198),
    "amd64": (0xC000003E, 41),
    "x86_64": (0xC000003E, 41),
}


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


def _network_seccomp_program(machine: str) -> bytes:
    try:
        audit_arch, socket_syscall = _SECCOMP_ARCHES[machine.lower()]
    except KeyError as exc:
        raise SandboxUnavailableError(
            f"--enumerate has no reviewed network-deny filter for Linux architecture {machine!r}"
        ) from exc

    instructions = [
        # Kill a process that changes syscall ABI after the filter is installed rather than
        # evaluating unfamiliar syscall numbers with the wrong architecture table.
        (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH_OFFSET),
        (_BPF_JMP_JEQ_K, 1, 0, audit_arch),
        (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
        (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_NR_OFFSET),
    ]
    if machine.lower() in {"amd64", "x86_64"}:
        # The x32 ABI shares AUDIT_ARCH_X86_64 but adds this bit to syscall numbers. Reject it
        # so it cannot bypass the native socket syscall rule.
        instructions.extend(
            (
                (_BPF_JMP_JGE_K, 0, 1, _X32_SYSCALL_BIT),
                (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
            )
        )
    instructions.extend(
        (
            (_BPF_JMP_JEQ_K, 0, 1, socket_syscall),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | EPERM),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        )
    )
    return b"".join(struct.pack("=HBBI", *instruction) for instruction in instructions)


def _open_network_seccomp() -> int:
    program = _network_seccomp_program(platform.machine())
    descriptor: int | None = None
    raw_memfd_create = getattr(os, "memfd_create", None)
    if raw_memfd_create is None:
        raise SandboxUnavailableError("the Linux runtime does not provide memfd_create")
    memfd_create = cast("Callable[[str, int], int]", raw_memfd_create)
    try:
        descriptor = memfd_create("charter-network-seccomp", _MFD_CLOEXEC)
        offset = 0
        while offset < len(program):
            offset += os.write(descriptor, program[offset:])
        os.lseek(descriptor, 0, os.SEEK_SET)
    except (AttributeError, OSError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise SandboxUnavailableError(
            f"the network-deny filter could not be prepared: {exc}"
        ) from exc
    return descriptor


def _probe_command(bubblewrap: str, seccomp_fd: int) -> list[str]:
    return [
        bubblewrap,
        "--ro-bind",
        "/usr",
        "/usr",
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
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "PATH",
        _FALLBACK_PATH,
        "--seccomp",
        str(seccomp_fd),
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

    seccomp_fd = _open_network_seccomp()
    try:
        try:
            probe = subprocess.run(
                _probe_command(bubblewrap, seccomp_fd),
                capture_output=True,
                check=False,
                env=_sanitized_environment(),
                pass_fds=(seccomp_fd,),
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxUnavailableError(
                f"Bubblewrap isolation could not be verified: {exc}"
            ) from exc
    finally:
        os.close(seccomp_fd)

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
    bubblewrap: str,
    project_root: Path,
    command: str,
    args: list[str],
    *,
    seccomp_fd: int | None = None,
) -> tuple[list[str], dict[str, str], tuple[int, ...]]:
    """Construct the only permitted launch vector for a configured stdio server."""
    root = project_root.resolve()
    sandbox_path = _sandbox_path(root)
    network_filter_fd = _open_network_seccomp() if seccomp_fd is None else seccomp_fd
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
        "--unshare-all",
        # Preserve the host network namespace only during Bubblewrap setup: GitHub's runner
        # rejects creation of the isolated loopback interface. The child still cannot create
        # any socket because the seccomp filter below is installed before it starts.
        "--share-net",
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
        # GitHub's current hosted Linux kernel rejects Bubblewrap's network-namespace
        # loopback setup. A fail-closed seccomp rule is stricter for this process: socket(2)
        # itself returns EPERM, so no IP or Unix socket can be created at all.
        "--seccomp",
        str(network_filter_fd),
        "--",
        _map_project_path(command, root),
        *(_map_project_path(arg, root) for arg in args),
    ]
    return launch, _sanitized_environment(root), (network_filter_fd,)
