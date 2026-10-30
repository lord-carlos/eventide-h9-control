from __future__ import annotations

from h9control.domain.knob_control import (
    FIRST_KNOB_CC,
    knob_cc_number,
    midi_cc_from_raw,
    ordered_knob_names,
    percent_from_raw,
    raw_from_midi_cc,
)
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT


def test_knob_cc_numbers_follow_hardware_order() -> None:
    # DIGDLY knob names reversed: MIX, DLYMIX, DLY-A, DLY-B, ...
    assert knob_cc_number("DIGDLY", "MIX") == FIRST_KNOB_CC
    assert knob_cc_number("DIGDLY", "DLY-A") == 24
    assert knob_cc_number("DIGDLY", "FILTER") == 31
    assert knob_cc_number("digdly", "dly-a") == 24
    assert knob_cc_number("DIGDLY", " dly-b ") == 25


def test_knob_cc_number_unknown_inputs() -> None:
    assert knob_cc_number(None, "DLY-A") is None
    assert knob_cc_number("DIGDLY", "") is None
    assert knob_cc_number("DIGDLY", "BOGUS") is None
    assert knob_cc_number("NOT-AN-ALGO", "DLY-A") is None


def test_ordered_knob_names_puts_first_cc_knob_first() -> None:
    names = ordered_knob_names("DIGDLY")
    assert names[0] == "MIX"
    assert names[-1] == "FILTER"
    assert len(names) == 10
    assert ordered_knob_names(None) == []


def test_cc_raw_round_trip_and_clamping() -> None:
    assert midi_cc_from_raw(0) == 0
    assert midi_cc_from_raw(MAX_KNOB_VALUE_14BIT) == 127
    assert midi_cc_from_raw(-10) == 0
    assert midi_cc_from_raw(MAX_KNOB_VALUE_14BIT + 10) == 127

    assert raw_from_midi_cc(0) == 0
    assert raw_from_midi_cc(127) == MAX_KNOB_VALUE_14BIT
    assert raw_from_midi_cc(-1) == 0
    assert raw_from_midi_cc(200) == MAX_KNOB_VALUE_14BIT

    # The CC grid is the shared truth: a raw value that is already on it
    # survives a round trip exactly.
    for cc in (0, 36, 64, 127):
        assert midi_cc_from_raw(raw_from_midi_cc(cc)) == cc


def test_percent_from_raw_clamps() -> None:
    assert percent_from_raw(0) == 0
    assert percent_from_raw(MAX_KNOB_VALUE_14BIT) == 100
    assert percent_from_raw(MAX_KNOB_VALUE_14BIT // 2) == 50
    assert percent_from_raw(-1) == 0
