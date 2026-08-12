from __future__ import annotations

import pytest

from h9control.domain.knob_display import (
    TimeDivision,
    format_knob_value,
    format_timefactor_dlymix,
    quantize_timefactor_delay_note,
    quantize_timefactor_delay_note_from_midi_cc,
    step_timefactor_delay_note_raw,
)
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT


def _pct_raw(percent: float) -> int:
    return int(round((percent / 100.0) * MAX_KNOB_VALUE_14BIT))


def test_quantize_delay_note_edges() -> None:
    assert quantize_timefactor_delay_note(0) == TimeDivision.OFF
    assert quantize_timefactor_delay_note(MAX_KNOB_VALUE_14BIT) == TimeDivision.N1_1


def test_quantize_delay_note_from_midi_cc() -> None:
    assert quantize_timefactor_delay_note_from_midi_cc(0) == TimeDivision.OFF
    assert quantize_timefactor_delay_note_from_midi_cc(59) == TimeDivision.N1_4
    assert quantize_timefactor_delay_note_from_midi_cc(124) == TimeDivision.N1_1
    assert quantize_timefactor_delay_note_from_midi_cc(127) == TimeDivision.N1_1


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (0.0, "A10 + B0"),
        (25.0, "A10 + B5"),
        (50.0, "A10 + B10"),
        (75.0, "A5 + B10"),
        (100.0, "A0 + B10"),
    ],
)
def test_format_timefactor_dlymix(percent: float, expected: str) -> None:
    assert format_timefactor_dlymix(_pct_raw(percent)) == expected


def test_format_delay_knob_returns_note_division() -> None:
    result = format_knob_value(algorithm_key="DIGDLY", knob_name="DLY-A", raw_value=0)
    assert result is not None
    assert result.label == "No DLY"
    assert result.division == TimeDivision.OFF

    result = format_knob_value(algorithm_key="DIGDLY", knob_name="dly-b", raw_value=_pct_raw(50.0))
    assert result is not None
    assert result.division is not None
    assert result.division in TimeDivision


def test_format_feedback_knob_scales_to_110_percent() -> None:
    result = format_knob_value(algorithm_key="VNTAGE", knob_name="FBK-A", raw_value=_pct_raw(50.0))
    assert result is not None
    assert result.label == "55%"

    result = format_knob_value(algorithm_key="VNTAGE", knob_name="FEEDBK", raw_value=MAX_KNOB_VALUE_14BIT)
    assert result is not None
    assert result.label == "110%"


def test_format_filter_knob_is_centered() -> None:
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="FILTER", raw_value=0).label == "-100"
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="FILTER", raw_value=_pct_raw(50.0)).label == "0"
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="FILTER", raw_value=MAX_KNOB_VALUE_14BIT).label == "100"


def test_format_speed_knob_in_hz() -> None:
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="SPEED", raw_value=0).label == "0.00 Hz"
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="SPEED", raw_value=MAX_KNOB_VALUE_14BIT).label == "5.01 Hz"


def test_format_dlymix_only_for_timefactor_algos() -> None:
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="DLYMIX", raw_value=_pct_raw(50.0)).label == "A10 + B10"
    assert format_knob_value(algorithm_key="HALL", knob_name="DLYMIX", raw_value=_pct_raw(50.0)) is None


def test_format_unknown_knob_returns_none() -> None:
    assert format_knob_value(algorithm_key="DIGDLY", knob_name="BOGUS", raw_value=1) is None
    assert format_knob_value(algorithm_key=None, knob_name="DLY-A", raw_value=1) is None


def test_step_delay_note_zero_delta_clamps() -> None:
    assert step_timefactor_delay_note_raw(-5, delta=0) == 0
    assert step_timefactor_delay_note_raw(MAX_KNOB_VALUE_14BIT + 5, delta=0) == MAX_KNOB_VALUE_14BIT


def test_step_delay_note_round_trip() -> None:
    start = 0
    stepped_up = step_timefactor_delay_note_raw(start, delta=1)
    assert stepped_up > start
    assert step_timefactor_delay_note_raw(stepped_up, delta=-1) == start


def test_step_delay_note_uses_delta_as_direction() -> None:
    assert step_timefactor_delay_note_raw(0, delta=5) == step_timefactor_delay_note_raw(0, delta=1)
    assert step_timefactor_delay_note_raw(0, delta=-5) == step_timefactor_delay_note_raw(0, delta=-1)


def test_step_delay_note_clamps_at_edges() -> None:
    assert step_timefactor_delay_note_raw(0, delta=-1) == 0

    top_raw = 0
    for _ in range(100):
        top_raw = step_timefactor_delay_note_raw(top_raw, delta=1)
    assert top_raw == step_timefactor_delay_note_raw(top_raw, delta=1)
    assert top_raw == step_timefactor_delay_note_raw(MAX_KNOB_VALUE_14BIT, delta=1)
