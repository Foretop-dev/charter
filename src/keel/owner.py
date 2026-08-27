"""CODEOWNERS resolution — moved from `apps/ebb/src/ebb/owner.py` (H7) so telltale and charter
can attribute their own file-anchored findings the same way ebb already does, rather than each
re-parsing the same file format. `git blame` stays product-specific (`ebb.owner.find_owner`) —
only ebb has a reliable per-occurrence line number to blame; telltale and charter anchor on a
file, not a line, so CODEOWNERS is the only owner source that applies to them."""

from pathlib import Path

import pathspec
from pathspec.pattern import Pattern

_CodeownersSpec = pathspec.PathSpec[Pattern]

# GitHub looks for CODEOWNERS in any of these three locations, root first.
_CODEOWNERS_LOCATIONS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def _find_codeowners_file(repo_root: Path) -> Path | None:
    for rel in _CODEOWNERS_LOCATIONS:
        candidate = repo_root / rel
        if candidate.is_file():
            return candidate
    return None


def _parse_codeowners(content: str) -> list[tuple[_CodeownersSpec, list[str]]]:
    # CODEOWNERS pattern syntax is documented by GitHub as "most of the same rules used for
    # .gitignore files" — reusing pathspec's gitignore matching here, same as keel.collect.walk's
    # nested-.gitignore handling, rather than reimplementing the same glob semantics twice.
    rules: list[tuple[_CodeownersSpec, list[str]]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        if not owners:
            continue
        rules.append((pathspec.PathSpec.from_lines("gitignore", [pattern]), owners))
    return rules


def owners_from_codeowners(path: Path, repo_root: Path) -> list[str] | None:
    """The last matching rule in the file wins — this is GitHub's own documented CODEOWNERS
    precedence (more specific / later rules override earlier ones), same direction as the
    nested-.gitignore precedence in keel.collect.walk but expressed as file order rather than
    directory depth, since CODEOWNERS is a single flat file."""
    codeowners_path = _find_codeowners_file(repo_root)
    if codeowners_path is None:
        return None

    rules = _parse_codeowners(codeowners_path.read_text(encoding="utf-8"))
    rel = str(path.resolve().relative_to(repo_root.resolve()))

    matched: list[str] | None = None
    for spec, owners in rules:
        if spec.match_file(rel):
            matched = owners
    return matched
