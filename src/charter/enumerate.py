import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from charter.models import Server, Transport
from charter.sandbox import build_bubblewrap_launch, require_bubblewrap

# modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle (fetched live this session):
# the client MUST send a protocolVersion it supports — "2025-06-18" is the last stable release
# before the 2026-07-28 draft's "modern" per-request-metadata redesign (server/discover instead
# of initialize/initialized) — a draft with essentially no real-world server support yet.
# Pinning to the classic initialize/initialized/tools/list handshake is a deliberate scope
# decision, not an oversight: virtually every MCP server actually deployed today speaks this
# one. specs/charter.md §11 already names protocol churn as a real, ongoing risk.
_PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "foretop-charter", "version": "0.2.1"}
_DEFAULT_TIMEOUT_SECONDS = 10.0
_SHUTDOWN_GRACE_SECONDS = 2.0

# Claude Code's own documented syntax (code.claude.com/docs/en/mcp, "Environment variable
# expansion in .mcp.json", fetched live this session): ${VAR} and ${VAR:-default}. The default
# value is matched non-greedily up to the first "}" — a default containing its own nested
# "${...}" isn't supported, a deliberate simplification (same "good enough, not semantically
# perfect" scope call this codebase already makes for PromQL/OTTL condition regexes).
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class EnumerationError(Exception):
    """Raised internally while speaking to a server; always caught and turned into an
    EnumerationResult.error string — enumeration failing for one server must never crash the
    whole scan (specs/telltale.md's own "degrade, don't crash" discipline, reused here)."""


@dataclass(frozen=True, slots=True)
class Tool:
    """One tool a server actually advertised via a real `tools/list` call — evidence live
    enumeration produces that static parsing never could (DEC-02). `input_schema` is kept as
    the server's own raw JSON Schema object: it's protocol metadata describing shape, not a
    secret, and a future capability classifier needs real property names to work from."""

    server_name: str
    name: str
    description: str | None
    input_schema: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class EnumerationResult:
    server_name: str
    tools: tuple[Tool, ...]
    error: str | None


def _expand_vars(text: str, env: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in env:
            return env[name]
        # Matches Claude Code's own documented degrade behavior exactly: a missing variable
        # with no default leaves the literal "${VAR}" text in place rather than failing the
        # whole config load.
        return default if default is not None else match.group(0)

    return _VAR_PATTERN.sub(_replace, text)


def _resolve_launch(server: Server) -> tuple[str, list[str]] | None:
    if server.command is None:
        return None
    # Live enumeration deliberately does not reproduce the MCP client's credential-bearing
    # environment. Defaults are still useful launch metadata, but real ${VAR} values and the
    # config's env values do not cross into an untrusted process.
    command = _expand_vars(server.command, {})
    args = [_expand_vars(a, {}) for a in server.args]
    return command, args


def _project_root(server: Server) -> Path:
    source = server.source_file.resolve()
    if source.name == "mcp.json" and source.parent.name == ".cursor":
        return source.parent.parent
    return source.parent


def _send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert proc.stdin is not None
    # One JSON-RPC message per line, no embedded newlines — the stdio transport's own framing
    # rule (modelcontextprotocol.io/specification/2025-06-18/basic/transports/stdio, fetched
    # live this session). json.dumps without indent never emits a literal newline.
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _receive(proc: subprocess.Popen[str]) -> dict[str, Any]:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        raise EnumerationError("server closed its output before responding")
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EnumerationError(f"invalid JSON-RPC message from server: {exc}") from exc
    if not isinstance(parsed, dict):
        raise EnumerationError("server sent a non-object JSON-RPC message")
    return parsed


def _parse_tool(server_name: str, raw: Any) -> Tool | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str):
        return None
    description = raw.get("description")
    input_schema = raw.get("inputSchema")
    return Tool(
        server_name=server_name,
        name=name,
        description=description if isinstance(description, str) else None,
        input_schema=input_schema if isinstance(input_schema, dict) else None,
    )


def _speak_mcp(proc: subprocess.Popen[str], server_name: str) -> tuple[Tool, ...]:
    _send(
        proc,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        },
    )
    init_response = _receive(proc)
    if "error" in init_response:
        raise EnumerationError(f"initialize failed: {init_response['error']}")

    # A notification (no "id") — the server sends no response to this one.
    _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools_response = _receive(proc)
    if "error" in tools_response:
        raise EnumerationError(f"tools/list failed: {tools_response['error']}")

    result = tools_response.get("result")
    raw_tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(raw_tools, list):
        raise EnumerationError("tools/list response had no tools array")

    parsed = (_parse_tool(server_name, t) for t in raw_tools)
    return tuple(t for t in parsed if t is not None)


def _shutdown(proc: subprocess.Popen[str]) -> None:
    # modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle's own shutdown sequence
    # for stdio: close stdin, wait, escalate to SIGTERM, then SIGKILL. Every step is best-effort
    # — a server that's already dead, hung, or ignoring signals must never leave charter itself
    # hanging or crashing.
    try:
        if proc.stdin:
            proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=_SHUTDOWN_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    proc.wait()


def enumerate_stdio_server(
    server: Server, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> EnumerationResult:
    """Launches `server` as a real subprocess and calls real `tools/list` over stdio — DEC-02's
    explicit "launching third-party servers is a security decision the user must make
    consciously" (specs/charter.md), so this is only ever called when `--enumerate` was passed.
    Protocol/target failures become `EnumerationResult.error`, with zero tools, so one bad
    server never aborts the rest of a scan. A missing or unusable isolation boundary raises
    SandboxUnavailableError instead: that is a global operational failure, never evidence
    about a particular server."""
    if server.transport != Transport.STDIO:
        return EnumerationResult(
            server.name, (), "not a stdio server — enumeration is stdio-only for now"
        )

    launch = _resolve_launch(server)
    if launch is None:
        return EnumerationResult(server.name, (), "could not re-read the original config entry")
    command, args = launch
    bubblewrap = require_bubblewrap()
    sandbox_argv, sandbox_env, sandbox_fds = build_bubblewrap_launch(
        bubblewrap, _project_root(server), command, args
    )

    try:
        try:
            proc = subprocess.Popen(
                sandbox_argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=sandbox_env,
                pass_fds=sandbox_fds,
                text=True,
                bufsize=1,
            )
        finally:
            for descriptor in sandbox_fds:
                os.close(descriptor)
    except OSError as exc:
        return EnumerationResult(server.name, (), f"failed to launch: {exc}")

    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            result_queue.put(("ok", _speak_mcp(proc, server.name)))
        except EnumerationError as exc:
            result_queue.put(("error", str(exc)))
        except Exception as exc:  # a hostile/broken server must never crash this thread
            result_queue.put(("error", f"unexpected error: {exc}"))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()

    try:
        status, payload = result_queue.get(timeout=timeout)
    except queue.Empty:
        _shutdown(proc)
        return EnumerationResult(server.name, (), f"timed out after {timeout}s")

    _shutdown(proc)
    if status == "error":
        return EnumerationResult(server.name, (), payload)
    return EnumerationResult(server.name, payload, None)
