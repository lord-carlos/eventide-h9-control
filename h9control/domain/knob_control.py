"""Knob <-> MIDI CC mapping and value conversions for the H9.

The H9 exposes effect knobs as Control Changes 22-31. The first CC (22) maps
to the *last* knob name in `H9FullAlgorithmData.knob_names()`, which lists
knobs bottom-left -> top-left. Reversing that list yields the hardware layout
order (top row left-to-right, then bottom row) and the CC order, so this module
owns both the ordering and the mapping.
"""

from __future__ import annotations

from h9control.domain.algorithms import H9FullAlgorithmData
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT

# MIDI CC range (0-127).
MAX_MIDI_CC_VALUE = 127

# CC number assigned to the first knob in hardware/CC order.
FIRST_KNOB_CC = 22


def midi_cc_from_raw(raw_value: int) -> int:
    """Convert a raw 14-bit knob value to a MIDI CC value (0-127)."""
    clamped = max(0, min(MAX_KNOB_VALUE_14BIT, int(raw_value)))
    return int(round((clamped / MAX_KNOB_VALUE_14BIT) * MAX_MIDI_CC_VALUE))


def raw_from_midi_cc(midi_cc: int) -> int:
    """Convert a MIDI CC value (0-127) back to a raw 14-bit knob value."""
    clamped = max(0, min(MAX_MIDI_CC_VALUE, int(midi_cc)))
    return int(round((clamped / MAX_MIDI_CC_VALUE) * MAX_KNOB_VALUE_14BIT))


def percent_from_raw(raw_value: int) -> int:
    """Convert a raw 14-bit knob value to a clamped percentage (0-100)."""
    clamped = max(0, min(MAX_KNOB_VALUE_14BIT, int(raw_value)))
    return int(round((clamped / MAX_KNOB_VALUE_14BIT) * 100.0))


def ordered_knob_names(algorithm_key: str | None) -> list[str]:
    """Return knob names in hardware order (CC 22 -> CC 31)."""
    return list(reversed(H9FullAlgorithmData.knob_names(algorithm_key or "")))


def knob_cc_number(algorithm_key: str | None, knob_name: str) -> int | None:
    """Return the H9 CC number for `knob_name`, or None when unmapped."""
    if not algorithm_key:
        return None

    wanted = knob_name.strip().upper()
    if not wanted:
        return None

    for index, name in enumerate(ordered_knob_names(algorithm_key)):
        if name.strip().upper() == wanted:
            return FIRST_KNOB_CC + index

    return None
