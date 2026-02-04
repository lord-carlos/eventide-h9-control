from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT


# MIDI CC range (0-127)
MAX_MIDI_CC_VALUE = 127


class TimeDivision(Enum):
    """All TimeFactor delay note divisions including triplets (T) and dotted (D)."""

    OFF = "No DLY"
    N1_64 = "1/64"
    N1_32T = "1/32 T"
    N1_64D = "1/64 D"
    N1_32 = "1/32"
    N1_16T = "1/16 T"
    N1_32D = "1/32 D"
    N1_16 = "1/16"
    N1_8T = "1/8 T"
    N1_16D = "1/16 D"
    N1_8 = "1/8"
    N1_4T = "1/4 T"
    N1_8D = "1/8 D"
    N1_4 = "1/4"
    N5_16 = "5/16"
    N1_2T = "1/2 T"
    N1_4D = "1/4 D"
    N7_16 = "7/16"
    N1_2 = "1/2"
    N9_16 = "9/16"
    N10_16 = "10/16"
    N1_1T = "WHOLE T"
    N11_16 = "11/16"
    N1_2D = "1/2 D"
    N13_16 = "13/16"
    N14_16 = "14/16"
    N15_16 = "15/16"
    N1_1 = "1/1"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class QuantizedValue:
    label: str
    division: TimeDivision | None = None


# Empirical mapping for TimeFactor tempo-synced delay time knobs.
#
# Maps MIDI CC values (0-127) to TimeDivision.
# Use the test script `scripts/test_delay_cc.py` to find exact switch points.
_TIMEFACTOR_DELAY_NOTE_POINTS: list[tuple[int, TimeDivision]] = [
    (0, TimeDivision.OFF),
    (3, TimeDivision.N1_64),
    (8, TimeDivision.N1_32T),
    (12, TimeDivision.N1_64D),
    (17, TimeDivision.N1_32),
    (22, TimeDivision.N1_16T),
    (26, TimeDivision.N1_32D),
    (31, TimeDivision.N1_16),
    (36, TimeDivision.N1_8T),
    (40, TimeDivision.N1_16D),
    (45, TimeDivision.N1_8),
    (50, TimeDivision.N1_4T),
    (54, TimeDivision.N1_8D),
    (59, TimeDivision.N1_4),
    (64, TimeDivision.N5_16),
    (68, TimeDivision.N1_2T),
    (73, TimeDivision.N1_4D),
    (78, TimeDivision.N7_16),
    (82, TimeDivision.N1_2),
    (87, TimeDivision.N9_16),
    (92, TimeDivision.N10_16),
    (96, TimeDivision.N1_1T),
    (101, TimeDivision.N11_16),
    (106, TimeDivision.N1_2D),
    (110, TimeDivision.N13_16),
    (115, TimeDivision.N14_16),
    (120, TimeDivision.N15_16),
    (124, TimeDivision.N1_1),
]

# Can probably be deleted at some point.
_TIMEFACTOR_ALGO_KEYS = {"DIGDLY", "VNTAGE", "TAPE", "MODDLY"}


def _pct_from_raw(value: int) -> float:
    """Convert raw 14-bit knob value to percentage (0-100)."""
    return (value / MAX_KNOB_VALUE_14BIT) * 100.0


def _midi_cc_from_raw(value: int) -> int:
    """Convert raw 14-bit knob value to MIDI CC value (0-127)."""
    return int(round((value / MAX_KNOB_VALUE_14BIT) * MAX_MIDI_CC_VALUE))


def _raw_from_midi_cc(midi_cc: int) -> int:
    """Convert MIDI CC value (0-127) to raw 14-bit knob value."""
    return int(round((midi_cc / MAX_MIDI_CC_VALUE) * MAX_KNOB_VALUE_14BIT))


def _pct_from_midi_cc(midi_cc: int) -> float:
    """Convert MIDI CC value (0-127) to percentage (0-100)."""
    return (midi_cc / MAX_MIDI_CC_VALUE) * 100.0


def quantize_timefactor_delay_note(value: int) -> TimeDivision:
    """Quantize a raw 14-bit knob value to the nearest TimeDivision.
    
    Uses the MIDI CC-based mapping in _TIMEFACTOR_DELAY_NOTE_POINTS.
    """
    midi_cc = _midi_cc_from_raw(value)
    best_div = _TIMEFACTOR_DELAY_NOTE_POINTS[0][1]
    best_dist = float("inf")
    for point_cc, div in _TIMEFACTOR_DELAY_NOTE_POINTS:
        dist = abs(midi_cc - point_cc)
        if dist < best_dist:
            best_dist = dist
            best_div = div
    return best_div


def quantize_timefactor_delay_note_from_midi_cc(midi_cc: int) -> TimeDivision:
    """Quantize a MIDI CC value (0-127) to the nearest TimeDivision."""
    best_div = _TIMEFACTOR_DELAY_NOTE_POINTS[0][1]
    best_dist = float("inf")
    for point_cc, div in _TIMEFACTOR_DELAY_NOTE_POINTS:
        dist = abs(midi_cc - point_cc)
        if dist < best_dist:
            best_dist = dist
            best_div = div
    return best_div


def format_timefactor_dlymix(value: int) -> str:
    """Format TimeFactor DLYMIX as the pedal-style `A10 + B6`.

    Assumption (matches user's observation):
    - At center (50%), both are 10.
    - Move left: B decreases 10 -> 0 while A stays 10.
    - Move right: A decreases 10 -> 0 while B stays 10.
    """

    pct = _pct_from_raw(value)
    if pct <= 50.0:
        a = 10
        b = int(round((pct / 50.0) * 10.0))
    else:
        b = 10
        a = int(round(((100.0 - pct) / 50.0) * 10.0))

    a = max(0, min(10, a))
    b = max(0, min(10, b))
    return f"A{a} + B{b}"


def format_knob_value(
    *,
    algorithm_key: str | None,
    knob_name: str,
    raw_value: int,
) -> QuantizedValue | None:
    """Optionally provide a human-readable rendering for specific knobs."""

    if algorithm_key is None:
        return None

    algo = algorithm_key.upper()
    name = knob_name.upper()

    # TimeFactor delay time knobs -> musical divisions
    if name in {"DLY-A", "DLY-B"}:
        div = quantize_timefactor_delay_note(raw_value)
        if div == TimeDivision.OFF:
            return QuantizedValue(label="No DLY", division=div)
        return QuantizedValue(label=str(div), division=div)

    # TimeFactor mix between A/B
    # TODO: I think this should be:
    # A10+B0 A10+B01 A10+B02 A10+B03 A10+B04 A10+B05 A10+B06 A10+B07 A10+B08 A10+B09 A10+B10 A09+B10 A08+B10 A07+B10 A06+B10 A05+B10 A04+B10 A03+B10 A02+B10 A01+B10 A00+B10
    if algo in _TIMEFACTOR_ALGO_KEYS and name == "DLYMIX":
        return QuantizedValue(label=format_timefactor_dlymix(raw_value), division=None)

    return None


def step_timefactor_delay_note_raw(current_raw: int, *, delta: int) -> int:
    """Step a tempo-synced TimeFactor delay knob in discrete musical divisions.

    Returns a new raw value in the same 0..MAX_KNOB_VALUE_14BIT space.
    """

    if delta == 0:
        return max(0, min(MAX_KNOB_VALUE_14BIT, current_raw))

    current_div = quantize_timefactor_delay_note(current_raw)
    divisions = [div for _, div in _TIMEFACTOR_DELAY_NOTE_POINTS]

    try:
        idx = divisions.index(current_div)
    except ValueError:
        idx = 0

    new_idx = max(0, min(len(divisions) - 1, idx + (1 if delta > 0 else -1)))
    target_midi_cc = _TIMEFACTOR_DELAY_NOTE_POINTS[new_idx][0]
    target_raw = _raw_from_midi_cc(target_midi_cc)
    return max(0, min(MAX_KNOB_VALUE_14BIT, target_raw))
