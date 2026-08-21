from charter.drift import Drift, DriftKind


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    # Same as _escape_data, plus : and , — those are structurally significant in the
    # `key=value,key=value` property list. Matches @actions/core's escapeData/escapeProperty
    # exactly (verified against actions/toolkit's own source, same contract apps/ebb's and
    # apps/telltale's own render/annotations.py already verified).
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def _message(drift: Drift) -> str:
    if drift.kind is DriftKind.NEW_SERVER:
        return f"charter: new MCP server {drift.server_name!r} — not present at the merge base."
    if drift.kind is DriftKind.NEW_TOOL:
        return (
            f"charter: {drift.server_name!r} gained tool {drift.tool_name!r} "
            f"({drift.after_severity}) — not present at the merge base."
        )
    return (
        f"charter: {drift.server_name!r} tool {drift.tool_name!r} severity went from "
        f"{drift.before_severity} to {drift.after_severity} since the merge base."
    )


def render_annotations(drift: tuple[Drift, ...]) -> str:
    """GitHub workflow-command syntax (`::error file=...,line=...::message`) — same
    zero-extra-permission mechanism as apps/ebb's and apps/telltale's own render/annotations.py:
    GitHub turns these into inline PR annotations on the Files Changed tab with no extra
    permissions and no separate API call, unlike SARIF upload (which needs
    `security-events: write` and, on private repos, GitHub Advanced Security/Code Security
    enabled — verified live against GitHub's own code-scanning docs this session). That's why
    this is the composite Action's default mechanism and SARIF (render/sarif.py) isn't wired
    into it — SARIF stays available for anyone who wants to add their own upload step.

    Only drift gets annotated, never the whole current scan: an existing server/tool that
    already existed at the merge base isn't new information for this PR — same reasoning
    apps/telltale's own render_annotations gives for regressions. Always `error`, never
    `warning`/`notice`: the drift gate (charter/drift.py) already decided this is worth failing
    the check over, so there's no lower severity to annotate at.

    Location is always the *server's* config source (`source_file`/`source_line`) — a tool has
    no line of its own (DEC-02: it comes from a live tools/list call, not static text), same
    anchor render/sarif.py already uses for the identical reason.
    """
    lines = []
    for d in drift:
        title = _escape_property(f"charter: {d.kind.value}")
        file_prop = _escape_property(d.source_file)
        message = _escape_data(_message(d))
        lines.append(f"::error file={file_prop},line={d.source_line},title={title}::{message}")
    return "\n".join(lines) + ("\n" if lines else "")
