"""GitHub Actions workflow-command rendering, shared by every product that annotates a PR.

Extracted in Session 30 (NEXT_STEPS.md §3.2's clearest case): `_escape_data` and
`_escape_property` were **byte-identical across four apps** — ebb, telltale, charter and
lading — with only their surrounding comments differing. That is the "genuinely identical, not
merely similar" bar `packages/keel/README.md` requires for a move here, met four times over.

The escaping contract matches `@actions/core`'s own `escapeData`/`escapeProperty`, verified
against actions/toolkit's own source when ebb first wrote it and re-confirmed by three
independent re-derivations since. It is a real wire format, not a guess: a raw `%`, newline or
carriage return in a message silently truncates or corrupts the annotation, and a raw `:` or
`,` inside a property value breaks the `key=value,key=value` list.
"""


def escape_data(text: str) -> str:
    """Escape a workflow-command *message* body."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Escape a workflow-command *property* value (`file=`, `line=`, `title=`).

    Same as `escape_data`, plus `:` and `,` — those are structurally significant in the
    `key=value,key=value` property list, so they need encoding there but not in the message
    body.
    """
    return escape_data(text).replace(":", "%3A").replace(",", "%2C")


def annotation(
    *,
    file: str,
    line: int | str,
    title: str,
    message: str,
    level: str = "error",
) -> str:
    """One `::level file=...,line=...,title=...::message` line, with every part escaped.

    Escaping happens here rather than at the call site because all four products were doing it
    by hand and a missed `escape_property` on a path containing `:` is a silent corruption, not
    a crash. `level` defaults to `error`: charter, telltale and lading all annotate only things
    their own gate already decided are worth failing the check over, so they have no lower
    severity to express. ebb is the one product that maps a real severity scale onto GitHub's
    three levels (error/warning/notice) and passes `level` explicitly.
    """
    return (
        f"::{level} file={escape_property(file)},line={line},"
        f"title={escape_property(title)}::{escape_data(message)}"
    )


def join_annotations(lines: list[str]) -> str:
    """Join rendered annotation lines, with a trailing newline only when there is output.

    The empty case matters: a step that prints a bare `"\\n"` when there is nothing to report
    reads as "something happened" in a CI log. All four products already had this exact
    expression; it moved here with the escapers rather than being left behind as the one
    duplicated line.
    """
    return "\n".join(lines) + ("\n" if lines else "")
