import pytest

from charter.canonical import NonCanonicalValueError, canonical_json


def test_keys_are_sorted_regardless_of_insertion_order() -> None:
    first = canonical_json({"b": 1, "a": 2})
    second = canonical_json({"a": 2, "b": 1})

    assert first == second
    assert first.index('"a"') < first.index('"b"')


def test_nested_keys_are_also_sorted() -> None:
    output = canonical_json({"outer": {"z": 1, "a": 2}})

    assert output.index('"a"') < output.index('"z"')


def test_a_top_level_float_is_rejected() -> None:
    with pytest.raises(NonCanonicalValueError):
        canonical_json(1.5)


def test_a_nested_float_in_a_dict_is_rejected() -> None:
    with pytest.raises(NonCanonicalValueError):
        canonical_json({"a": {"b": 1.5}})


def test_a_float_inside_a_list_is_rejected() -> None:
    with pytest.raises(NonCanonicalValueError):
        canonical_json({"a": [1, 2, 3.0]})


def test_an_int_is_not_mistaken_for_a_float() -> None:
    # bool is technically a subclass of int in Python, and 3.0 == 3 — make sure the isinstance
    # check is doing the right thing and not accidentally rejecting plain ints.
    canonical_json({"a": 3, "b": True, "c": [1, 2, 3]})


def test_output_ends_with_exactly_one_trailing_newline() -> None:
    output = canonical_json({"a": 1})

    assert output.endswith("\n")
    assert not output.endswith("\n\n")


def test_output_never_contains_a_carriage_return() -> None:
    output = canonical_json({"a": "value\nwith\nnewlines"})

    assert "\r" not in output


def test_non_ascii_characters_are_not_escaped() -> None:
    output = canonical_json({"name": "café"})

    assert "café" in output
    assert "\\u00e9" not in output
