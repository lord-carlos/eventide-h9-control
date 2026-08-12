from __future__ import annotations

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
