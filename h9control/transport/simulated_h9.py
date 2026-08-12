from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from queue import Empty, Queue

import mido

from h9control.domain.algorithms import H9FullAlgorithmData
from h9control.protocol.codes import H9SysexCodes, H9SystemKeys, MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import (
    build_eventide_sysex,
    decode_eventide_sysex,
)


@dataclass(frozen=True)
class SimulatedPreset:
    """A small fixture representing one H9 program."""

    algorithm_key: str
    preset_name: str
    category: int
    effect_index: int
    knob_values: tuple[int, ...]


_PRESETS = (
    SimulatedPreset(
        algorithm_key="DIGDLY",
        preset_name="PRISTINE DIGITAL",
        category=1,
        effect_index=0,
        knob_values=(
            0x2600,
            0x3600,
            0x4200,
            0x3000,
            0x5200,
            0x4800,
            0x3000,
            0x2400,
            0x4000,
            0x5000,
        ),
    ),
    SimulatedPreset(
        algorithm_key="VNTAGE",
        preset_name="WARM ECHO",
        category=1,
        effect_index=1,
        knob_values=(
            0x1800,
            0x2C00,
            0x3A00,
            0x4400,
            0x5A00,
            0x3C00,
            0x2000,
            0x1800,
            0x3800,
            0x4800,
        ),
    ),
    SimulatedPreset(
        algorithm_key="TAPE",
        preset_name="DARK TAPE",
        category=1,
        effect_index=2,
        knob_values=(
            0x4800,
            0x2400,
            0x3000,
            0x5000,
            0x4600,
            0x5800,
            0x1800,
            0x1200,
            0x3600,
            0x4200,
        ),
    ),
    SimulatedPreset(
        algorithm_key="MODDLY",
        preset_name="MOTION DELAY",
        category=1,
        effect_index=3,
        knob_values=(
            0x2200,
            0x5000,
            0x3C00,
            0x2A00,
            0x4000,
            0x4C00,
            0x2800,
            0x1C00,
            0x3A00,
            0x4A00,
        ),
    ),
)


class SimulatedH9Transport:
    """In-memory H9 that answers the protocol used by the dashboard."""

    def __init__(self, *, device_id: int = 1, initial_program: int = 0) -> None:
        self._device_id = device_id
        self._current_program = initial_program % len(_PRESETS)
        self._knob_values = list(_PRESETS[self._current_program].knob_values)
        self._bpm_x100 = 12500
        self._incoming: Queue[mido.Message] = Queue()
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def send_sysex(self, framed_or_unframed: Sequence[int] | bytes | bytearray) -> None:
        if self._closed:
            raise RuntimeError("Simulated H9 is closed")

        data = list(framed_or_unframed)
        if data and data[0] == 0xF0:
            data = data[1:]
        if data and data[-1] == 0xF7:
            data = data[:-1]

        frame = decode_eventide_sysex(mido.Message("sysex", data=data))
        if frame is None or frame.device_id not in (0, self._device_id):
            return

        if frame.command == H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT:
            self._enqueue(H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP, self._program_dump())
        elif frame.command == H9SysexCodes.SYSEXC_VALUE_WANT:
            self._handle_value_want(frame.payload)
        elif frame.command == H9SysexCodes.SYSEXC_VALUE_PUT:
            self._handle_value_put(frame.payload)

    def receive_pending(self) -> list[mido.Message]:
        messages: list[mido.Message] = []
        while True:
            try:
                messages.append(self._incoming.get_nowait())
            except Empty:
                return messages

    def send_program_change(self, program: int, channel: int = 0) -> None:
        if not 0 <= program <= 127:
            raise ValueError("program must be 0..127")
        if not 0 <= channel <= 15:
            raise ValueError("channel must be 0..15")

        self._current_program = program % len(_PRESETS)
        self._knob_values = list(_PRESETS[self._current_program].knob_values)

    def send_control_change(self, control: int, value: int, channel: int = 0) -> None:
        if not 0 <= control <= 127:
            raise ValueError("control must be 0..127")
        if not 0 <= value <= 127:
            raise ValueError("value must be 0..127")
        if not 0 <= channel <= 15:
            raise ValueError("channel must be 0..15")

        knob_index = control - 22
        if not 0 <= knob_index < 10:
            return

        knob_names = list(
            reversed(H9FullAlgorithmData.knob_names(self._current_preset.algorithm_key))
        )
        knob_name = knob_names[knob_index]
        original_names = H9FullAlgorithmData.knob_names(
            self._current_preset.algorithm_key
        )
        original_index = original_names.index(knob_name)
        self._knob_values[original_index] = int(
            round((value / 127.0) * MAX_KNOB_VALUE_14BIT)
        )

    @property
    def _current_preset(self) -> SimulatedPreset:
        return _PRESETS[self._current_program]

    def _program_dump(self) -> bytes:
        preset = self._current_preset
        knob_text = " ".join(f"{value:X}" for value in self._knob_values)
        return (
            f"[{self._current_program + 1}] {preset.effect_index} 2 {preset.category}\r\n"
            f"0 {knob_text} 7F\r\n"
            f"C_SIM{self._current_program + 1:02d}\r\n"
            f"{preset.algorithm_key}\r\n"
            f"{preset.preset_name}\r\n\x00"
        ).encode("ascii")

    def _handle_value_want(self, payload: bytes) -> None:
        try:
            key = int(payload.decode("ascii").strip(), 16)
        except (UnicodeDecodeError, ValueError):
            return

        if key == H9SystemKeys.KEY_SP_TEMPO:
            self._enqueue(
                H9SysexCodes.SYSEXC_VALUE_DUMP,
                f"{key:X} {self._bpm_x100}".encode("ascii"),
            )

    def _handle_value_put(self, payload: bytes) -> None:
        parts = payload.decode("ascii", errors="ignore").split()
        if len(parts) < 2:
            return

        try:
            key = int(parts[0], 16)
            value = int(parts[1], 16)
        except ValueError:
            return

        if key == H9SystemKeys.KEY_SP_TEMPO:
            self._bpm_x100 = value

    def _enqueue(self, command: int, payload: bytes) -> None:
        data = build_eventide_sysex(self._device_id, command, payload)
        self._incoming.put(mido.Message("sysex", data=data[1:-1]))
