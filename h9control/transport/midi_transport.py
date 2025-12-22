from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from typing import Any, cast

import mido

from midi import H9Midi
from h9control.protocol.sysex import format_sysex_bytes


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConnectionInfo:
    output_name: str
    input_name: str | None


class MidiTransport:
    """Thin wrapper around `H9Midi` with an app-friendly interface."""

    def __init__(self, midi: H9Midi) -> None:
        self._midi = midi

    def list_output_ports(self) -> list[str]:
        return self._midi.list_ports().outputs

    def connect(self) -> ConnectionInfo:
        output_name = self._midi.connect()
        ports = self._midi.list_ports()
        input_name = next((name for name in ports.inputs if name.startswith(self._midi.device_prefix)), None)
        logger.info("Connected MIDI: output=%r input=%r", output_name, input_name)
        return ConnectionInfo(output_name=output_name, input_name=input_name)

    def close(self) -> None:
        logger.info("Closing MIDI transport")
        self._midi.close()

    def send_sysex(self, framed_or_unframed: Sequence[int] | bytes | bytearray) -> None:
        try:
            data_list = list(framed_or_unframed)  # type: ignore[arg-type]
        except TypeError:
            data_list = list(bytes(framed_or_unframed))  # type: ignore[arg-type]

        logger.debug("TX sysex: %s", format_sysex_bytes(data_list))
        self._midi.send_sysex(framed_or_unframed)

    def receive_pending(self) -> list[mido.Message]:
        messages = self._midi.receive_pending()
        for msg in messages:
            m = cast(Any, msg)
            if getattr(m, "type", None) == "sysex":
                logger.debug("RX sysex: %s", format_sysex_bytes(list(m.data)))
        return messages

    def send_program_change(self, program: int, channel: int = 0) -> None:
        if program < 0 or program > 127:
            raise ValueError("program must be 0..127")
        if channel < 0 or channel > 15:
            raise ValueError("channel must be 0..15")

        # Send directly through the opened output port.
        # (H9Midi currently only exposes SysEx send.)
        out = getattr(self._midi, "_out", None)
        if out is None:
            raise RuntimeError("MIDI output not connected. Call connect() first.")

        out.send(mido.Message("program_change", program=program, channel=channel))
