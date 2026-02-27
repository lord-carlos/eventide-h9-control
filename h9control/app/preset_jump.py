from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from h9control.transport.midi_transport import MidiTransport


logger = logging.getLogger(__name__)


class PresetJump:
    """Handles jumping to specific presets via MIDI Program Change."""

    def __init__(self, transport: MidiTransport, midi_channel: int = 0) -> None:
        self._transport = transport
        self._midi_channel = midi_channel

    def jump_to_preset(self, program: int) -> None:
        """Jump to a specific preset using MIDI Program Change.

        Args:
            program: Program number (0-127). Preset 1 = program 0.
        """
        if not self._transport:
            logger.warning("Transport not connected, cannot jump to preset")
            return

        try:
            self._transport.send_program_change(
                program=program, channel=self._midi_channel
            )
            logger.info("Jumped to preset %d (program %d)", program + 1, program)
        except Exception as exc:
            logger.exception("Program change failed: %s", exc)
            raise
