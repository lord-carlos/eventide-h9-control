from __future__ import annotations

import logging

import pytest

from h9control.domain.preset import parse_preset_dump_text


DIGDLY_KNOB_LINE = "0 2600 3600 4200 3000 5200 4800 3000 2400 4000 5000 7F"


def _dump(*lines: str) -> str:
    return "\r\n".join(lines) + "\r\n\x00"


def test_parses_full_bracketed_dump() -> None:
    preset = parse_preset_dump_text(
        _dump(
            "[1] 0 2 1",
            DIGDLY_KNOB_LINE,
            "C_AB12CD",
            "DIGDLY",
            "PRISTINE DIGITAL",
        )
    )

    assert preset.preset_number == 1
    assert preset.effect_index == 0
    assert preset.dump_format == 2
    assert preset.category == 1
    assert preset.effect_number == 0
    assert preset.algorithm_name == "DIGDLY"
    assert preset.preset_name == "PRISTINE DIGITAL"
    assert preset.checksum == "C_AB12CD"
    assert preset.algorithm_key == "DIGDLY"
    assert preset.pedal_value == 0x7F
    assert preset.knob_values == [0x2600, 0x3600, 0x4200, 0x3000, 0x5200, 0x4800, 0x3000, 0x2400, 0x4000, 0x5000]
    assert preset.knobs_by_name is not None
    assert preset.knobs_by_name["DLY-A"] == 0x2400
    assert preset.knobs_by_name["MIX"] == 0x5000


def test_parses_unbracketed_header() -> None:
    preset = parse_preset_dump_text(_dump("3 2 1 4", DIGDLY_KNOB_LINE, "C_X", "DIGDLY", "NAME"))

    assert preset.preset_number == 3
    assert preset.effect_index == 2
    assert preset.dump_format == 1
    assert preset.category == 4


def test_header_with_missing_fields() -> None:
    preset = parse_preset_dump_text(_dump("[5] 1", DIGDLY_KNOB_LINE))

    assert preset.preset_number == 5
    assert preset.effect_index == 1
    assert preset.dump_format is None
    assert preset.category is None


def test_hex_effect_number_on_knob_line() -> None:
    preset = parse_preset_dump_text(_dump("[1] 0 2 4", "b 2600 3600 4200 3000 5200 4800 3000 2400 4000 5000 7F"))

    assert preset.effect_number == 11
    assert preset.effect_number_raw == 11


def test_short_dump_keeps_partial_knob_values_without_mapping() -> None:
    preset = parse_preset_dump_text(_dump("[1] 0 2 1", "0 2600 3600 4200", "C_X", "DIGDLY", "NAME"))

    assert preset.knob_values == [0x2600, 0x3600, 0x4200]
    assert preset.knobs_by_name is None


def test_overrange_knob_values_are_clamped_to_14bit_max() -> None:
    # Tokens > 0x7FE0 must not reach the dashboard as >100% bars.
    preset = parse_preset_dump_text(
        _dump(
            "[1] 0 2 1",
            "0 FFFE 7FE1 2600 3600 4200 3000 5200 4800 3000 7F",
            "C_X",
            "DIGDLY",
            "NAME",
        )
    )

    assert preset.knob_values == [0x7FE0, 0x7FE0, 0x2600, 0x3600, 0x4200, 0x3000, 0x5200, 0x4800, 0x3000, 0x7F]
    assert preset.pedal_value is None  # only 10 value tokens in this strip
    assert preset.knobs_by_name is not None


def test_negative_knob_tokens_clamp_to_zero(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="h9control.domain.preset"):
        preset = parse_preset_dump_text(
            _dump("[1] 0 2 1", "0 -8 2600 3600 4200 3000 5200 4800 3000 2400 4000 7F", "C_X", "DIGDLY", "NAME")
        )

    assert preset.knob_values[0] == 0
    assert preset.knob_values[1] == 0x2600


def test_short_dump_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="h9control.domain.preset"):
        parse_preset_dump_text(_dump("[1] 0 2 1", "0 2600 3600 4200", "C_X", "DIGDLY", "NAME"))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("only 3 knob value(s)" in r.getMessage() for r in warnings)


def test_full_dump_does_not_log_knob_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="h9control.domain.preset"):
        parse_preset_dump_text(_dump("[1] 0 2 1", DIGDLY_KNOB_LINE, "C_X", "DIGDLY", "NAME"))

    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_fallback_to_category_index_when_name_missing() -> None:
    preset = parse_preset_dump_text(_dump("[7] 2 2 1", DIGDLY_KNOB_LINE, "C_X"))

    assert preset.algorithm_key == "TAPE"
    assert preset.algorithm_name == "TAPE"
    assert preset.knobs_by_name is not None
    assert "FILTER" in preset.knobs_by_name


def test_shimmr_dump_maps_all_algorithm_knobs_by_name() -> None:
    preset = parse_preset_dump_text(
        _dump(
            "[8] 11 2 4",
            "b 1000 2000 3000 4000 5000 6000 7000 4000 5000 6000 7F",
            "C_X",
            "SHIMMR",
            "ETHEREAL",
        )
    )

    assert preset.algorithm_key == "SHIMMR"
    assert preset.knobs_by_name is not None
    assert list(preset.knobs_by_name) == [
        "MIDDCY",
        "PITCH",
        "PICH-B",
        "PICH-A",
        "HI-DCY",
        "LO-DCY",
        "DELAY",
        "SIZE",
        "DECAY",
        "MIX",
    ]


def test_fallback_uses_hex_effect_number_when_header_index_out_of_range() -> None:
    preset = parse_preset_dump_text(_dump("[1] 12 2 2", "6 2600 3600 4200 3000 5200 4800 3000 2400 4000 5000 7F"))

    assert preset.algorithm_key == "TREMLO_MOD"


def test_single_trailing_line_is_algorithm_name() -> None:
    preset = parse_preset_dump_text(_dump("[1] 0 2 4", DIGDLY_KNOB_LINE, "C_X", "BLACKHOLE"))

    assert preset.algorithm_name == "BLACKHOLE"
    assert preset.algorithm_key == "BKHOLE"
    assert preset.preset_name is None


def test_single_trailing_line_is_preset_name() -> None:
    preset = parse_preset_dump_text(_dump("[1] 0 2 1", DIGDLY_KNOB_LINE, "C_X", "MY CUSTOM"))

    assert preset.preset_name == "MY CUSTOM"
    assert preset.algorithm_name == "DIGDLY"
    assert preset.algorithm_key == "DIGDLY"


def test_checksum_line_missing_still_parses_knobs() -> None:
    preset = parse_preset_dump_text(_dump("[1] 0 2 1", DIGDLY_KNOB_LINE))

    assert preset.checksum is None
    assert preset.algorithm_key == "DIGDLY"
    assert preset.knobs_by_name is not None


def test_empty_input_returns_empty_snapshot() -> None:
    preset = parse_preset_dump_text("")
    assert preset.preset_number is None
    assert preset.algorithm_key is None
    assert preset.knobs_by_name is None


@pytest.mark.parametrize(
    "raw",
    [
        "\x00",
        "   \r\n\r\n  ",
        "\x00[1] 0 2 1\x00\r\n\x00" + DIGDLY_KNOB_LINE + "\r\n\x00",
    ],
)
def test_tolerant_of_nulls_and_whitespace(raw: str) -> None:
    preset = parse_preset_dump_text(raw)
    assert isinstance(preset.raw_text, str)
