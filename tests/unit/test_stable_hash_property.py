import json

from hypothesis import given, settings
from hypothesis import strategies as st

from charter.collect import collect
from charter.lockfile import render_lock

# specs/charter.md: "The manifest must be byte-stable for unchanged input — sort everything, no
# timestamps inside the hashed content, or every PR shows a spurious diff and adoption dies in
# week one. Property-test this first." This is that property test, not just a fixed-fixture
# regression test — it generates arbitrary (valid-shaped) .mcp.json documents and proves the
# pipeline is deterministic across the space of realistic inputs, not just the one example a
# human happened to write by hand.

# Printable ASCII only: Hypothesis's default st.text() can generate lone surrogates and other
# codepoints that raise UnicodeEncodeError when charter.lockfile.write_lock later encodes to
# UTF-8 — a real constraint of the encoding step, unrelated to the byte-stability property this
# test exists to check, so it's excluded here rather than becoming an unrelated test failure.
_SAFE_TEXT = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=20)

_SERVER_NAME = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=1, max_size=15
).filter(lambda s: s != "__line__")

_STDIO_ENTRY = st.fixed_dictionaries(
    {
        "command": _SAFE_TEXT,
        "args": st.lists(_SAFE_TEXT, max_size=5),
        "env": st.dictionaries(_SAFE_TEXT, _SAFE_TEXT, max_size=5),
    }
)

_REMOTE_ENTRY = st.fixed_dictionaries(
    {
        "type": st.sampled_from(["http", "streamable-http", "sse", "ws"]),
        "url": _SAFE_TEXT.map(lambda s: f"https://{s}"),
        "headers": st.dictionaries(_SAFE_TEXT, _SAFE_TEXT, max_size=5),
    }
)

_SERVERS = st.dictionaries(_SERVER_NAME, st.one_of(_STDIO_ENTRY, _REMOTE_ENTRY), max_size=5)


@given(servers=_SERVERS)
@settings(max_examples=100)
def test_two_scans_of_unchanged_config_are_byte_identical(servers: dict, tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    # tmp_path_factory, not the function-scoped tmp_path fixture: Hypothesis calls this test
    # body many times per test item, and tmp_path is only resolved once per item by pytest —
    # tmp_path_factory.mktemp() gives each example its own real fresh directory instead.
    root = tmp_path_factory.mktemp("charter-property")
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))

    first = render_lock(collect(root), root)
    second = render_lock(collect(root), root)

    assert first == second


@given(servers=_SERVERS)
@settings(max_examples=50)
def test_manifest_always_round_trips_as_valid_json(servers: dict, tmp_path_factory) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path_factory.mktemp("charter-property")
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))

    rendered = render_lock(collect(root), root)

    json.loads(rendered)  # must not raise
