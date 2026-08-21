from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Transport(StrEnum):
    """How a client reaches an MCP server — the transport shapes both Claude Code's and
    Cursor's config formats recognize (modelcontextprotocol.io/specification and each client's
    own docs, fetched live this session): STDIO launches a local subprocess; HTTP/SSE/WS all
    reach a remote server over the network. SSE is deprecated per the MCP spec's own transport
    docs but still seen in real configs (Claude Code still accepts it), so it stays a real
    value here rather than being folded into HTTP."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"
    WS = "ws"


@dataclass(frozen=True, slots=True)
class Server:
    """One MCP server declaration from an agent config file. Static parsing only knows what the
    config itself says — how to reach the server, and the *names* of the environment variables
    and headers it wires in — never what tools the server actually exposes (that needs live
    enumeration: DEC-02, Slice 2) and never the *values* of those variables/headers (DEC-06's
    hard invariant — see env_var_names/header_names below).

    `command`/`args` are populated for STDIO servers, `url` for HTTP/SSE/WS — never both,
    per each client's own config shape, but neither is type-narrowed to enforce that here: a
    real-world config can be malformed (a stdio entry with a stray `url` key, say), and the
    parser's job is to record what a file actually says, not to pre-validate it into a shape
    that hides that.

    `args` is captured verbatim, unlike `env_var_names`/`header_names` — a real, considered
    choice, not an oversight found the hard way (`charter scan` against a fixture whose args
    included `--dsn postgresql://readonly:pass@host/db`, a literal password in a positional
    argument, not an env var). DEC-06's own default is "names and paths only... never attempt
    to detect actual secret values" — extending that same non-detection stance to `args` (not
    just `env`/`headers`) is consistent with it, not a gap in it: a config file already
    committed to the repo (DEC-04's whole premise) has already put that string in git history
    regardless of what charter does with it, and guessing at which positional argument "looks
    like" a secret is exactly the value-detection DEC-06 explicitly declined to build. A
    capability classifier (Slice 2) is the right place to flag "this arg looks credential-
    shaped" as a heuristic *finding* a reviewer sees — not a reason for the collector to hide
    evidence a reviewer needs to understand what the server actually does."""

    name: str
    transport: Transport
    command: str | None
    args: tuple[str, ...]
    env_var_names: tuple[str, ...]
    url: str | None
    header_names: tuple[str, ...]
    source_file: Path
    source_line: int


@dataclass(frozen=True, slots=True)
class ServerSet:
    servers: tuple[Server, ...]
