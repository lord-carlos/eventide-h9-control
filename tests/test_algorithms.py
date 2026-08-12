from __future__ import annotations

import pytest

from h9control.domain.algorithms import H9FullAlgorithmData


def test_tremolo_algorithms_have_distinct_category_keys_and_knobs() -> None:
    mod_key = H9FullAlgorithmData.resolve_key_from_category_index(2, 6)
    space_key = H9FullAlgorithmData.resolve_key_from_category_index(4, 9)

    assert mod_key == "TREMLO_MOD"
    assert space_key == "TREMLO_SPC"
    assert H9FullAlgorithmData.knob_names(mod_key) == [
        "MODSRC",
        "RATE",
        "S-MOD",
        "D-MOD",
        "WIDTH",
        "SHAPE",
        "SPEED",
        "DEPTH",
        "TYPE",
        "INTENS",
    ]
    assert H9FullAlgorithmData.knob_names(space_key) == [
        "HIFREQ",
        "STDPTH",
        "SPEED",
        "SHAPE",
        "HI-LVL",
        "LO-LVL",
        "PREDLY",
        "SIZE",
        "DECAY",
        "MIX",
    ]


def test_tremolo_display_names_resolve_with_category() -> None:
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("TREMOLOPAN", 2)
        == "TREMLO_MOD"
    )
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("TREMOLOVERB", 4)
        == "TREMLO_SPC"
    )
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("TREMLO", 2)
        == "TREMLO_MOD"
    )
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("TREMLO", 4)
        == "TREMLO_SPC"
    )
    assert H9FullAlgorithmData.resolve_key_from_display_name("TREMLO") is None


def test_every_algorithm_has_exactly_ten_knobs() -> None:
    for key, meta in H9FullAlgorithmData.ALGO_MAP.items():
        assert len(meta.knobs) == 10, f"{key} has {len(meta.knobs)} knobs"


def test_every_algorithm_has_unique_knob_names() -> None:
    for key, meta in H9FullAlgorithmData.ALGO_MAP.items():
        assert len(set(meta.knobs)) == len(meta.knobs), f"{key} has duplicate knobs"


def test_every_algorithm_belongs_to_exactly_one_category() -> None:
    all_cat_keys: list[str] = []
    for cat in H9FullAlgorithmData.CATEGORIES.values():
        all_cat_keys.extend(cat.keys)

    assert len(all_cat_keys) == len(set(all_cat_keys))
    assert set(all_cat_keys) == set(H9FullAlgorithmData.ALGO_MAP)


def test_every_category_index_resolves_to_a_real_algorithm() -> None:
    for cat_number, cat in H9FullAlgorithmData.CATEGORIES.items():
        for index, key in enumerate(cat.keys):
            assert (
                H9FullAlgorithmData.resolve_key_from_category_index(cat_number, index)
                == key
            )


def test_ambiguous_display_name_requires_category() -> None:
    assert H9FullAlgorithmData.resolve_key_from_display_name("REVERSE") is None
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("REVERSE", 1) == "REVERS"
    )
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("REVERSE", 4) == "REVRVB"
    )


def test_display_name_normalizes_spaces_and_case() -> None:
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name("Digital Delay", 1)
        == "DIGDLY"
    )
    assert (
        H9FullAlgorithmData.resolve_key_from_display_name(" VINTAGE DELAY ", 1)
        == "VNTAGE"
    )


def test_unknown_display_name_returns_none() -> None:
    assert H9FullAlgorithmData.resolve_key_from_display_name("NOT AN ALGO", 1) is None
    assert H9FullAlgorithmData.resolve_key_from_display_name("", 1) is None


def test_unknown_category_index_returns_none() -> None:
    assert H9FullAlgorithmData.resolve_key_from_category_index(99, 0) is None
    assert H9FullAlgorithmData.resolve_key_from_category_index(1, 99) is None
    assert H9FullAlgorithmData.resolve_key_from_category_index(None, 0) is None
    assert H9FullAlgorithmData.resolve_key_from_category_index(1, None) is None
    assert H9FullAlgorithmData.resolve_key_from_category_index(None, None) is None


def test_unknown_category_in_display_name_resolution_returns_none() -> None:
    assert H9FullAlgorithmData.resolve_key_from_display_name("DIGITALDELAY", 99) is None
    assert H9FullAlgorithmData.resolve_key_from_display_name("DIGDLY", 99) == "DIGDLY"


def test_get_info_for_known_and_unknown_keys() -> None:
    info = H9FullAlgorithmData.get_info("digdly")
    assert info["description"]
    assert info["knobs"] == H9FullAlgorithmData.knob_names("DIGDLY")

    missing = H9FullAlgorithmData.get_info("NOPE")
    assert missing["description"] == "Algorithm not found."
    assert missing["knobs"] == []
    assert missing["display_names"] == []


def test_knob_names_for_unknown_key_is_empty() -> None:
    assert H9FullAlgorithmData.knob_names("NOPE") == []


def test_knob_names_returns_copy() -> None:
    names = H9FullAlgorithmData.knob_names("DIGDLY")
    names.append("BOGUS")
    assert "BOGUS" not in H9FullAlgorithmData.knob_names("DIGDLY")


def test_resolve_key_from_numbers_is_backward_compatible() -> None:
    assert (
        H9FullAlgorithmData.resolve_key_from_numbers(1, 0)
        == H9FullAlgorithmData.resolve_key_from_category_index(1, 0)
        == "DIGDLY"
    )

