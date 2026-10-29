"""MIDI transport for the Eventide H9 (moved here from the root `midi` module).

The root `midi.py` remains as a thin compatibility shim that re-exports
`H9Midi` and `MidiPorts` from this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import logging
from typing import Optional
from typing import Any, cast

import mido

__all__ = ["MidiPorts", "H9Midi"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MidiPorts:
    inputs: list[str]
    outputs: list[str]


class H9Midi:
    """MIDI transport for sending SysEx to an Eventide H9.

    - Lists available MIDI ports.
    - Auto-connects to the first port whose name starts with `device_prefix`.
    - Sends SysEx messages created by the protocol layer.

    Notes on mido SysEx:
    - `mido.Message('sysex', data=...)` expects data WITHOUT 0xF0 and 0xF7.
    - This class accepts either framed (F0...F7) or unframed payloads.
    """

    def __init__(
        self,
        device_prefix: str = "H9 Pedal",
        *,
        backend: str = "mido.backends.rtmidi",
        input_enabled: bool = True,
    ) -> None:
        self.device_prefix = device_prefix
        self.backend = backend
        self.input_enabled = input_enabled

        # Ensure a backend that works on Windows. This is a no-op if already set.
        mido.set_backend(self.backend)

        self._out: Optional[mido.ports.BaseOutput] = None
        self._in: Optional[mido.ports.BaseInput] = None

    def list_ports(self) -> MidiPorts:
        m = cast(Any, mido)
        return MidiPorts(inputs=m.get_input_names(), outputs=m.get_output_names())

    def connect(self) -> str:
        """Connect to the first output port starting with `device_prefix`.

        Returns the output port name.
        """
        # Close any previously opened handles so repeated connect() calls
        # don't leak orphaned open MIDI ports.
        self.close()

        m = cast(Any, mido)
        outputs = m.get_output_names()
        output_name = next((name for name in outputs if name.startswith(self.device_prefix)), None)
        if output_name is None:
            raise RuntimeError(
                f"No MIDI output port starts with {self.device_prefix!r}. "
                f"Available outputs: {outputs}"
            )

        self._out = m.open_output(output_name)

        try:
            if self.input_enabled:
                inputs = m.get_input_names()
                input_name = next((name for name in inputs if name.startswith(self.device_prefix)), None)
                if input_name is not None:
                    self._in = m.open_input(input_name)
        except Exception:
            # Don't leak the output handle when the input side fails to open.
            self.close()
            raise

        return output_name

    def is_port_present(self) -> bool:
        """Return True if an output port matching `device_prefix` is currently available.

        Port enumeration is not cached: the device can appear/disappear at any
        time (USB hotplug). Enumeration failures are reported as "absent".
        """
        try:
            outputs = cast(Any, mido).get_output_names()
        except Exception:
            logger.debug("Failed to enumerate MIDI output ports", exc_info=True)
            return False

        return any(name.startswith(self.device_prefix) for name in outputs)

    def close(self) -> None:
        """Close both handles, independently and without raising.

        Safe to call repeatedly; callers use this during teardown/shutdown.
        """
        self._close_port("input", self._in)
        self._close_port("output", self._out)
        self._in = None
        self._out = None

    @staticmethod
    def _close_port(kind: str, port: Optional[mido.ports.BasePort]) -> None:
        if port is None:
            return
        try:
            port.close()
        except Exception:
            logger.warning("Failed to close MIDI %s port", kind, exc_info=True)

    def send_sysex(self, data: Sequence[int] | bytes | bytearray) -> None:
        """Send a SysEx message.

        Accepts either:
        - Full framed message: [0xF0, ..., 0xF7]
        - Unframed data payload for mido: [...]
        """
        if self._out is None:
            raise RuntimeError("MIDI output not connected. Call connect() first.")

        data_list = self._to_int_list(data)

        # Strip F0/F7 if present.
        if data_list and data_list[0] == 0xF0:
            data_list = data_list[1:]
        if data_list and data_list[-1] == 0xF7:
            data_list = data_list[:-1]

        msg = mido.Message("sysex", data=data_list)
        self._out.send(msg)

    def send_program_change(self, program: int, channel: int = 0) -> None:
        """Send a MIDI Program Change message.

        Args:
            program: Program number (0-127)
            channel: MIDI channel (0-15, 0-based as in mido)
        """
        if program < 0 or program > 127:
            raise ValueError("program must be 0..127")
        if channel < 0 or channel > 15:
            raise ValueError("channel must be 0..15")
        if self._out is None:
            raise RuntimeError("MIDI output not connected. Call connect() first.")

        self._out.send(mido.Message("program_change", program=program, channel=channel))

    def send_control_change(self, control: int, value: int, channel: int = 0) -> None:
        """Send a MIDI Control Change message.

        Args:
            control: CC number (0-127)
            value: CC value (0-127)
            channel: MIDI channel (0-15, 0-based as in mido)
        """
        if control < 0 or control > 127:
            raise ValueError("control must be 0..127")
        if value < 0 or value > 127:
            raise ValueError("value must be 0..127")
        if channel < 0 or channel > 15:
            raise ValueError("channel must be 0..15")
        if self._out is None:
            raise RuntimeError("MIDI output not connected. Call connect() first.")

        self._out.send(mido.Message("control_change", control=control, value=value, channel=channel))

    def receive_pending(self) -> list[mido.Message]:
        """Return any pending incoming MIDI messages (if input was opened)."""
        if self._in is None:
            return []

        messages: list[mido.Message] = []
        while True:
            msg = self._in.poll()
            if msg is None:
                break
            messages.append(msg)
        return messages

    @staticmethod
    def _to_int_list(data: Sequence[int] | bytes | bytearray) -> list[int]:
        if isinstance(data, (bytes, bytearray)):
            return list(data)

        if isinstance(data, Iterable):
            out: list[int] = []
            for b in data:
                if not isinstance(b, int):
                    raise TypeError(f"SysEx data items must be ints (0-255); got {type(b)}")
                if b < 0 or b > 255:
                    raise ValueError(f"SysEx byte out of range (0-255): {b}")
                out.append(b)
            return out

        raise TypeError(f"Unsupported SysEx data type: {type(data)}")
