"""R20: live tool enumeration over Streamable HTTP.

`specs/charter.md` FR-002 asked for enumeration "via stdio **and** HTTP transports" and only
stdio shipped, so an HTTP server was silently skipped before enumeration and collapsed into the
same "not enumerated" answer as one nobody asked to enumerate. Measured across four real
repositories with committed configs, HTTP is not an edge case: nuxt.com declares three servers
and every one is HTTP, so charter could say nothing at all about it.

Unlike stdio this launches no local code, so it needs no Bubblewrap and works on any platform —
but it does reach out to a third-party endpoint, which is its own conscious decision and stays
behind the same `--enumerate` opt-in.

Every wire detail here is from the 2025-06-18 Streamable HTTP spec fetched live
(modelcontextprotocol.io/specification/2025-06-18/basic/transports), not written from memory:
the client MUST POST to the endpoint, MUST send `Accept: application/json, text/event-stream`,
MUST handle a response that is either `application/json` or `text/event-stream`, MUST echo an
`Mcp-Session-Id` if initialization returned one, and MUST send `MCP-Protocol-Version` on
subsequent requests.
"""

import json
from pathlib import Path

import httpx
import pytest

from charter.enumerate_http import enumerate_http_server
from charter.models import Server, Transport

ENDPOINT = "https://mcp.example.invalid/mcp"


def _server(url: str = ENDPOINT, transport: Transport = Transport.HTTP) -> Server:
    return Server(
        name="remote",
        transport=transport,
        command=None,
        args=(),
        env_var_names=(),
        url=url,
        header_names=(),
        source_file=Path(".mcp.json"),
        source_line=3,
    )


def _json_rpc_result(request: httpx.Request, tools: list[dict[str, object]]) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("method") == "initialize":
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
        )
    if body.get("method") == "tools/list":
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}}
        )
    return httpx.Response(202)  # a notification, per the spec


def test_a_json_responding_server_is_enumerated() -> None:
    tools = [{"name": "search_docs", "description": "Search the docs", "inputSchema": {}}]
    transport = httpx.MockTransport(lambda r: _json_rpc_result(r, tools))

    result = enumerate_http_server(_server(), transport=transport)

    assert result.error is None
    assert [t.name for t in result.tools] == ["search_docs"]
    assert result.tools[0].server_name == "remote"


def test_the_required_headers_are_sent_on_every_request() -> None:
    """`Accept` must list both content types on every POST; `MCP-Protocol-Version` is required
    on requests after initialization, and sending it throughout is permitted and simpler."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _json_rpc_result(request, [])

    enumerate_http_server(_server(), transport=httpx.MockTransport(handler))

    assert seen, "no request was made"
    for request in seen:
        accept = request.headers["accept"]
        assert "application/json" in accept
        assert "text/event-stream" in accept
        assert request.headers["mcp-protocol-version"] == "2025-06-18"
        assert request.method == "POST"
        assert str(request.url) == ENDPOINT


def test_a_session_id_returned_at_initialization_is_echoed_on_later_requests() -> None:
    """Spec: a server MAY assign a session at initialization via `Mcp-Session-Id`, and a client
    that receives one MUST send it on every subsequent request. A server requiring it answers
    400 without it, so failing to echo would break enumeration against every stateful server."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            response = _json_rpc_result(request, [])
            response.headers["Mcp-Session-Id"] = "1868a90c"
            return response
        if request.headers.get("mcp-session-id") != "1868a90c":
            return httpx.Response(400, text="missing session id")
        return _json_rpc_result(request, [{"name": "ok"}])

    result = enumerate_http_server(_server(), transport=httpx.MockTransport(handler))

    assert result.error is None, result.error
    assert [t.name for t in result.tools] == ["ok"]
    assert len(seen) >= 3
    assert all(r.headers.get("mcp-session-id") == "1868a90c" for r in seen[1:])


def test_an_sse_responding_server_is_enumerated() -> None:
    """The spec lets a server answer a request with either one JSON object or an SSE stream,
    and says the client MUST support both. A real deployment picking SSE must not read as a
    broken server."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") is None or "id" not in body:
            return httpx.Response(202)
        payload = (
            {"protocolVersion": "2025-06-18"}
            if body["method"] == "initialize"
            else {"tools": [{"name": "from_sse"}]}
        )
        message = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": payload})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=f"event: message\ndata: {message}\n\n",
        )

    result = enumerate_http_server(_server(), transport=httpx.MockTransport(handler))

    assert result.error is None, result.error
    assert [t.name for t in result.tools] == ["from_sse"]


def test_an_authenticating_server_reports_a_real_error_not_a_crash() -> None:
    """Most public MCP endpoints require OAuth, which charter deliberately does not implement.
    That must surface as a stated reason a reader can act on, not an exception and not silence."""
    transport = httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized"))

    result = enumerate_http_server(_server(), transport=transport)

    assert result.tools == ()
    assert result.error is not None
    assert "401" in result.error


def test_a_transport_failure_is_reported_as_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed")

    result = enumerate_http_server(_server(), transport=httpx.MockTransport(handler))

    assert result.tools == ()
    assert result.error is not None


def test_a_stdio_server_is_refused_rather_than_silently_returning_nothing() -> None:
    result = enumerate_http_server(_server(url=None, transport=Transport.STDIO))

    assert result.tools == ()
    assert result.error is not None
    assert "http" in result.error.lower()


def test_a_jsonrpc_error_from_tools_list_is_reported() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "initialize":
            return _json_rpc_result(request, [])
        if body.get("method") == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        return httpx.Response(202)

    result = enumerate_http_server(_server(), transport=httpx.MockTransport(handler))

    assert result.tools == ()
    assert result.error is not None
    assert "tools/list" in result.error


@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher"])
def test_a_non_http_url_is_refused(scheme: str) -> None:
    """`file://` in particular would make an enumeration flag into a local file read."""
    result = enumerate_http_server(_server(url=f"{scheme}://example.invalid/mcp"))

    assert result.tools == ()
    assert result.error is not None
