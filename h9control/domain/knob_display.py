from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT


class TimeDivision(Enum):
    OFF = "OFF"
    N1_64 = "1/64"
    N1_32 = "1/32"
    N1_16 = "1/16"
    N1_8 = "1/8"
    N1_4 = "1/4"
    N1_2 = "1/2"
    N1_1 = "1/1"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class QuantizedValue:
    label: str
    division: TimeDivision | None = None


# Empirical/UX-oriented mapping for TimeFactor tempo-synced delay time knobs.
#
# This is not guaranteed to match every H9 firmware/version perfectly, but it
# aligns with observed values:
# - ~37.5% shows as 1/8
# - ~50% shows as 1/4
# - ~65% shows as 1/2
# - 100% shows as 1/1
_TIMEFACTOR_DELAY_NOTE_POINTS: list[tuple[float, TimeDivision]] = [
    (0.0, TimeDivision.OFF),
    (12.5, TimeDivision.N1_64),
    (25.0, TimeDivision.N1_32),
    (31.25, TimeDivision.N1_16),
    (37.5, TimeDivision.N1_8),
    (50.0, TimeDivision.N1_4),
    (66.6667, TimeDivision.N1_2),
    (100.0, TimeDivision.N1_1),
]


_TIMEFACTOR_ALGO_KEYS = {"DIGDLY", "VNTAGE", "TAPE", "MODDLY"}


def _pct_from_raw(value: int) -> float:
    return (value / MAX_KNOB_VALUE_14BIT) * 100.0


def quantize_timefactor_delay_note(value: int) -> TimeDivision:
    pct = _pct_from_raw(value)
    best_div = _TIMEFACTOR_DELAY_NOTE_POINTS[0][1]
    best_dist = float("inf")
    for point_pct, div in _TIMEFACTOR_DELAY_NOTE_POINTS:
        dist = abs(pct - point_pct)
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
    if algo in _TIMEFACTOR_ALGO_KEYS and name in {"DLY-A", "DLY-B"}:
        div = quantize_timefactor_delay_note(raw_value)
        if div == TimeDivision.OFF:
            return QuantizedValue(label="OFF", division=div)
        return QuantizedValue(label=f"{div} note", division=div)

    # TimeFactor mix between A/B
    if algo in _TIMEFACTOR_ALGO_KEYS and name == "DLYMIX":
        return QuantizedValue(label=format_timefactor_dlymix(raw_value), division=None)

    return None
