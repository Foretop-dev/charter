"""The Console-into-StringIO/`no_color` shell every product's own terminal renderer already
used, byte-identical across all six call sites (ebb, telltale x2, charter x2, lading) before
this extraction — NEXT_STEPS.md §3.2. No shared "document shell" beyond this: each product's
own `Table`(s) and column layout stay exactly where they are, same "genuinely identical, not
merely similar" bar `keel.render.markdown`/`keel.render.annotations` were already held to.
"""

from collections.abc import Sequence
from io import StringIO

from rich.console import Console
from rich.table import Table


def render_tables(tables: Sequence[Table], summary: str, *, no_color: bool = False) -> str:
    """Renders one or more tables plus a trailing summary line into a returned string.

    `no_color` matters beyond tests: the caller (a CLI) has already decided whether the real
    destination is a terminal via `sys.stdout.isatty()` and passes that decision down. Rendering
    into an internal `StringIO` buffer, which is never itself a tty, means Rich's own terminal
    autodetection can't be trusted — `force_terminal=not no_color` makes rendering depend only
    on the explicit `no_color` argument, not on ambient environment variables Rich also consults
    (e.g. `FORCE_COLOR`), which made this non-deterministic across machines: a local shell with
    such a variable set produced colored output by accident while CI, with no tty and no such
    variable, silently produced plain output either way.
    """
    buffer = StringIO()
    console = Console(
        file=buffer,
        width=120,
        no_color=no_color,
        highlight=not no_color,
        force_terminal=not no_color,
    )
    for table in tables:
        console.print(table)
    console.print(summary)
    return buffer.getvalue()
