import json
from typing import Any


class NonCanonicalValueError(ValueError):
    """Raised when a value would make the manifest byte-unstable. specs/charter.md §6:
    "canonical JSON: sorted keys, no floats, LF endings, trailing newline." A float's text
    representation is not guaranteed stable the way an int's or a string's is (trailing zeros,
    exponent notation can vary), so refusing to serialize one at all is safer than hoping
    repr() never changes underneath this tool."""


def _assert_no_floats(value: Any) -> None:
    if isinstance(value, float):
        raise NonCanonicalValueError(f"canonical JSON forbids floats, got {value!r}")
    if isinstance(value, dict):
        for v in value.values():
            _assert_no_floats(v)
    elif isinstance(value, list | tuple):
        for v in value:
            _assert_no_floats(v)


def canonical_json(data: Any) -> str:
    """specs/charter.md §6: "Manifest serialisation is canonical JSON: sorted keys, no floats,
    LF endings, trailing newline. Test byte-equality across two runs." Sorted keys
    (`json.dumps`' own `sort_keys=True`, recursive) makes dict-insertion-order irrelevant; no
    floats forbids a value whose text form isn't guaranteed stable; the trailing newline is
    added here. LF-only line endings are the caller's job when writing bytes to disk
    (`lockfile.py` uses `Path.write_bytes`, never a text-mode handle that could translate `\\n`
    to `\\r\\n` on Windows) — `json.dumps` itself only ever produces `\\n` internally, so there
    is nothing to translate here, only downstream at the OS file-write boundary.
    """
    _assert_no_floats(data)
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
