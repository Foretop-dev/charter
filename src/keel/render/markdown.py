"""Markdown table primitives, shared by every product that posts a PR comment.

Extracted in Session 30 alongside `keel.render.annotations` (NEXT_STEPS.md §3.2). The
`escape_cell` body was byte-identical in charter and lading; `row`/`divider` existed once, in
telltale, as genuinely generic table primitives with nothing product-specific in them.

Checking the §3.2 claim turned up something the table did not record: **ebb and telltale were
not escaping their table cells at all.** ebb interpolates a model id, a file path and an owner
straight into a row; telltale interpolates a route template and an error-mode string. A literal
`|` in any of them silently breaks the table's column count for the whole row, and these are
not all trusted values — a model id comes from a registry, a file path from the scanned repo.
So this module is a small bug fix as well as a de-duplication.

There is deliberately **no shared "document shell"** here, despite §3.2's original wording.
Each product's markdown document is genuinely its own shape — ebb is one table, telltale is two
tables plus a regressions section, charter is tool rows plus drift, lading is a table plus an
embedded Mermaid diagram. A common shell would be an invented abstraction, not a removed
duplication, and `SUITE_ARCHITECTURE.md` §8 is explicit about not doing that.
"""


def escape_cell(text: str) -> str:
    """Make `text` safe to place inside a markdown table cell.

    Three real injection surfaces, all observed in values this suite actually renders:
    a literal `|` breaks the row's column count; a newline ends the row entirely; a backtick
    lets a value escape the `` `code` `` span it was meant to sit inside.
    """
    return text.replace("|", "\\|").replace("\n", " ").replace("`", "'")


def row(cells: list[str]) -> str:
    """One `| a | b |` table row. Cells are used as given — call `escape_cell` first for
    anything that isn't a literal you control."""
    return "| " + " | ".join(cells) + " |"


def divider(column_count: int) -> str:
    """The `|---|---|` separator under a header row."""
    return "|" + "---|" * column_count
