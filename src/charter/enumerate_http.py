"""Live tool enumeration over the MCP Streamable HTTP transport.

R20, and the second half of `specs/charter.md` FR-002 ("live tool enumeration via stdio **and**
HTTP transports") — only stdio shipped, so `cli._enumerate_all` skipped every remote server
before it reached an enumerator and the result collapsed into the same "not enumerated" answer
as a server nobody asked about. That is not an edge case: measured across four real repositories
with committed configs, nuxt.com declares three servers and all three are HTTP.

Two things make this materially different from `enumerate.py`, and both cut in charter's favour:

* **No local code is launched**, so there is no sandbox to require. `enumerate.py` needs Linux
  and Bubblewrap because it executes a third party's program; this only speaks JSON-RPC to a URL
  the repository already committed, so it works on any platform.
* **It does reach the network**, which is its own conscious decision. It stays behind the same
  `--enumerate` opt-in for that reason, and sends no credentials: `Server.header_names` holds
  names, never values (DEC-06), so an authenticating endpoint is expected to refuse and that
  refusal is reported as a stated reason rather than swallowed.

Every wire detail is transcribed from the 2025-06-18 specification fetched live
(modelcontextprotocol.io/specification/2025-06-18/basic/transports), per CLAUDE.md's rule
against writing a contract from memory. Specifically: the client MUST POST each JSON-RPC message
to the endpoint; MUST send `Accept` listing both `application/json` and `text/event-stream`; MUST
handle a response that is either a single JSON object or an SSE stream; MUST echo an
`Mcp-Session-Id` on subsequent requests if initialization returned one; and MUST send
`MCP-Protocol-Version` on requests after initialization.

Deliberately not implemented: OAuth (`apps/charter/README.md` already names an OAuth-capable
client as real scope creep), the deprecated 2024-11-05 HTTP+SSE transport and its `endpoint`-
event discovery dance, GET-initiated streams, and session teardown via DELETE. A server needing
any of those reports an error, which is a true statement about what charter established.
"""

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from charter.enumerate import (
    _CLIENT_INFO,
    _DEFAULT_TIMEOUT_SECONDS,
    _PROTOCOL_VERSION,
    EnumerationError,
    EnumerationResult,
    Tool,
    _parse_tool,
)
from charter.models import Server, Transport

_HTTP_TRANSPORTS = frozenset({Transport.HTTP, Transport.SSE})
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# The spec requires both, on every POST — a server may answer either way and the client has to
# accept whichever it picks.
_ACCEPT = "application/json, text/event-stream"


def _sse_payload(text: str) -> dict[str, Any]:
    """The first JSON-RPC message carried in an SSE body.

    Only `data:` lines matter here: the stream may also carry `event:`, `id:` and comments, and
    a server MAY send unrelated requests or notifications before the response. Taking the first
    frame that parses as a JSON object with an `id` or `error` is enough for a single
    request/response exchange, which is all this module performs.
    """
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "".join(
            line[len("data:") :].strip() for line in block.split("\n") if line.startswith("data:")
        )
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            return parsed
    raise EnumerationError("SSE stream carried no JSON-RPC response")


def _read_message(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        return _sse_payload(response.text)
    try:
        parsed = response.json()
    except ValueError as exc:
        raise EnumerationError(f"response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise EnumerationError("server sent a non-object JSON-RPC message")
    return parsed


def _post(
    client: httpx.Client, url: str, message: dict[str, Any], headers: dict[str, str]
) -> httpx.Response:
    response = client.post(url, json=message, headers=headers)
    if response.status_code >= 400:
        raise EnumerationError(f"HTTP {response.status_code} from {url}")
    return response


def _speak_mcp_http(client: httpx.Client, url: str, server_name: str) -> tuple[Tool, ...]:
    headers = {"Accept": _ACCEPT, "MCP-Protocol-Version": _PROTOCOL_VERSION}

    init = _post(
        client,
        url,
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
        headers,
    )
    # A server MAY establish a session here; if it does, every later request must carry the id
    # or a stateful server answers 400.
    session_id = init.headers.get("Mcp-Session-Id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    init_message = _read_message(init)
    if "error" in init_message:
        raise EnumerationError(f"initialize failed: {init_message['error']}")

    # A notification carries no id and the server answers 202 with no body — nothing to read.
    _post(client, url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)

    list_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    listed = _read_message(_post(client, url, list_request, headers))
    if "error" in listed:
        raise EnumerationError(f"tools/list failed: {listed['error']}")

    result = listed.get("result")
    raw_tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(raw_tools, list):
        raise EnumerationError("tools/list response had no tools array")

    parsed = (_parse_tool(server_name, tool) for tool in raw_tools)
    return tuple(tool for tool in parsed if tool is not None)


def enumerate_http_server(
    server: Server,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> EnumerationResult:
    """Calls a real `tools/list` against a remote MCP endpoint.

    Never raises: every failure — a refused connection, an authenticating endpoint, a malformed
    response — becomes `EnumerationResult.error`, exactly as `enumerate_stdio_server` does, so a
    reachable server and an unreachable one are distinguishable states rather than one silence.

    `transport` exists for tests to supply an `httpx.MockTransport`; production passes nothing.
    """
    if server.transport not in _HTTP_TRANSPORTS:
        return EnumerationResult(
            server_name=server.name,
            tools=(),
            error=f"not an http server — this enumerator speaks http only, got "
            f"{server.transport.value}",
        )
    if not server.url:
        return EnumerationResult(
            server_name=server.name, tools=(), error="no url declared for this server"
        )
    # A `file://` url would otherwise turn an enumeration flag into a local file read.
    scheme = urlparse(server.url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        return EnumerationResult(
            server_name=server.name,
            tools=(),
            error=f"refusing to enumerate a non-http url scheme: {scheme or 'none'}",
        )

    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True) as client:
            tools = _speak_mcp_http(client, server.url, server.name)
    except EnumerationError as exc:
        return EnumerationResult(server_name=server.name, tools=(), error=str(exc))
    except httpx.HTTPError as exc:
        return EnumerationResult(
            server_name=server.name, tools=(), error=f"{type(exc).__name__}: {exc}"
        )
    return EnumerationResult(server_name=server.name, tools=tools, error=None)
