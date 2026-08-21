#!/usr/bin/env python3
"""A real, minimal MCP server speaking genuine newline-delimited JSON-RPC over stdio — no MCP
SDK, no charter code, hand-written from the same spec pages charter/enumerate.py itself was
built against. Exists purely so charter's live-enumeration client can be tested against a real
subprocess exchanging real protocol messages, not a mocked stdin/stdout — the same "verify
against something real" discipline this whole build has used throughout (ebb's cold uvx
install, telltale's real git worktree tests).

Usage: python3 toy_mcp_server.py [--fail-tools-list] [--sleep-forever] [--crash-on-init]
"""

import json
import sys
import time

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from disk",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file to disk",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
]


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main() -> None:
    args = sys.argv[1:]

    if "--sleep-forever" in args:
        time.sleep(3600)
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method")
        request_id = request.get("id")

        if method == "initialize":
            if "--crash-on-init" in args:
                sys.exit(1)
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "toy-mcp-server", "version": "0.0.1"},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue  # a notification — no response
        elif method == "tools/list":
            if "--fail-tools-list" in args:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32603, "message": "internal error, on purpose"},
                    }
                )
            else:
                _send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})


if __name__ == "__main__":
    main()
