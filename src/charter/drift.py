from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from charter.capability import Severity

_SEVERITY_RANK = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class DriftKind(StrEnum):
    """specs/charter.md's "capability diff" (FR-006), scoped to exactly the two things worth
    failing a PR check over — mirrors telltale's own regression-only philosophy (never an
    absolute threshold, only "did this get worse since the merge base"). A server disappearing,
    a tool disappearing, or a severity *decreasing* is never reported — same asymmetry
    telltale's compute_regressions already established: only movement in the worse direction
    is drift."""

    NEW_SERVER = "new_server"
    NEW_TOOL = "new_tool"
    SEVERITY_INCREASED = "severity_increased"


@dataclass(frozen=True, slots=True)
class Drift:
    server_name: str
    source_file: str
    source_line: int
    tool_name: str | None  # None for NEW_SERVER — the whole server is the news, not one tool.
    kind: DriftKind
    before_severity: str | None
    after_severity: str | None


def _server_key(server: dict[str, Any]) -> tuple[str, str]:
    return server["source_file"], server["name"]


def _tools_by_name(server: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    tools = server["tools"]
    if tools is None:
        return None
    return {t["name"]: t for t in tools}


def compute_drift(before: dict[str, Any] | None, after: dict[str, Any]) -> tuple[Drift, ...]:
    """Pure: (manifest, manifest) -> Drift list. Both are `lockfile.to_manifest()`-shaped dicts
    — `after` from the current scan, `before` from `json.loads(git_ref.show_file_at_ref(...))`
    at the merge base. `before is None` (no baseline found — a brand new charter.lock, or a
    non-default --lock path that was never committed) means nothing to compare, not
    "everything is new": returns `()`, the same null/empty/populated discipline `lockfile.py`'s
    own `tools: null` already encodes for a single server, applied here to the whole baseline.
    """
    if before is None:
        return ()

    before_by_key = {_server_key(s): s for s in before["servers"]}
    drifts: list[Drift] = []

    for server in after["servers"]:
        before_server = before_by_key.get(_server_key(server))
        if before_server is None:
            drifts.append(
                Drift(
                    server_name=server["name"],
                    source_file=server["source_file"],
                    source_line=server["source_line"],
                    tool_name=None,
                    kind=DriftKind.NEW_SERVER,
                    before_severity=None,
                    after_severity=None,
                )
            )
            continue

        after_tools = _tools_by_name(server)
        before_tools = _tools_by_name(before_server)
        if after_tools is None or before_tools is None:
            # Tool-level drift needs enumeration evidence on *both* sides — a server that
            # wasn't --enumerate'd this run, or wasn't at the baseline commit, has nothing
            # honest to compare (not "no tools", not "no drift" — just unknown).
            continue

        for tool_name, tool in after_tools.items():
            before_tool = before_tools.get(tool_name)
            if before_tool is None:
                drifts.append(
                    Drift(
                        server_name=server["name"],
                        source_file=server["source_file"],
                        source_line=server["source_line"],
                        tool_name=tool_name,
                        kind=DriftKind.NEW_TOOL,
                        before_severity=None,
                        after_severity=tool["severity"],
                    )
                )
                continue

            before_severity = Severity(before_tool["severity"])
            after_severity = Severity(tool["severity"])
            if _SEVERITY_RANK[after_severity] > _SEVERITY_RANK[before_severity]:
                drifts.append(
                    Drift(
                        server_name=server["name"],
                        source_file=server["source_file"],
                        source_line=server["source_line"],
                        tool_name=tool_name,
                        kind=DriftKind.SEVERITY_INCREASED,
                        before_severity=before_tool["severity"],
                        after_severity=tool["severity"],
                    )
                )

    return tuple(drifts)
