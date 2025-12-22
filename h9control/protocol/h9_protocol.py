from __future__ import annotations

import logging
import time

from h9control.domain.preset import PresetSnapshot, parse_preset_dump_text
from h9control.protocol.codes import H9SysexCodes
from h9control.protocol.sysex import SysexFrame, build_eventide_sysex, decode_eventide_sysex
from h9control.transport.midi_transport import MidiTransport


class H9Protocol:
    """High-level protocol operations for an H9.

    This class deals with:
    - sending Eventide SysEx commands
    - waiting for matching responses
    - parsing a minimal `PresetSnapshot`
    """

    # Commands (from protocol constants)
    SYSEXC_TJ_PROGRAM_WANT = H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT
    SYSEXC_TJ_PROGRAM_DUMP = H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP

    def __init__(self, transport: MidiTransport, *, device_id: int = 1) -> None:
        self._transport = transport
        self.device_id = device_id

        self._logger = logging.getLogger(self.__class__.__name__)

    def request_current_program(self, *, timeout_s: float = 2.0) -> PresetSnapshot:
        """Request and parse the currently loaded preset/program."""

        self._logger.info("Requesting current program (0x%02X)", self.SYSEXC_TJ_PROGRAM_WANT)
        msg = build_eventide_sysex(self.device_id, self.SYSEXC_TJ_PROGRAM_WANT)
        self._transport.send_sysex(msg)

        frame = self._wait_for_command(self.SYSEXC_TJ_PROGRAM_DUMP, timeout_s=timeout_s)

        self._logger.info(
            "Received program dump (0x%02X), payload_len=%d", frame.command, len(frame.payload)
        )

        # The dump payload is typically ASCII-ish; decode tolerantly.
        raw_text = frame.payload.decode("ascii", errors="replace")
        self._logger.debug("Program dump text (first 300 chars): %r", raw_text[:300])
        preset = parse_preset_dump_text(raw_text)

        if preset.algorithm_key is not None:
            self._logger.debug("Resolved algorithm_key: %s", preset.algorithm_key)
        if preset.knobs_by_name is not None:
            self._logger.debug("Knobs: %s", preset.knobs_by_name)

        return preset

    def set_parameter(self, key: int, value: int | str) -> None:
        """Set a parameter via SYSEXC_VALUE_PUT (0x2D).

        Payload format used here matches the project's earlier implementation:
        - key sent as ASCII hex (no 0x)
        - a single space separator
        - value sent as ASCII decimal string

        Note: The critical missing piece for many parameters is discovering the correct key IDs.
        """

        key_str = f"{key:X}"
        val_str = str(value)

        payload = bytearray()
        payload.extend(key_str.encode("ascii"))
        payload.append(0x20)  # space
        payload.extend(val_str.encode("ascii"))

        self._logger.info("Setting parameter key=0x%X value=%s", key, val_str)
        msg = build_eventide_sysex(self.device_id, H9SysexCodes.SYSEXC_VALUE_PUT, payload)
        self._transport.send_sysex(msg)

    def _wait_for_command(self, command: int, *, timeout_s: float) -> SysexFrame:
        deadline = time.monotonic() + timeout_s
        last_seen_frames: list[SysexFrame] = []

        while time.monotonic() < deadline:
            for msg in self._transport.receive_pending():
                frame = decode_eventide_sysex(msg)
                if frame is None:
                    continue

                # Accept broadcast responses (device_id 0) or exact match.
                if frame.device_id not in (0, self.device_id):
                    continue

                last_seen_frames.append(frame)
                self._logger.debug(
                    "RX Eventide frame: device_id=%d cmd=0x%02X payload_len=%d",
                    frame.device_id,
                    frame.command,
                    len(frame.payload),
                )
                if frame.command == command:
                    return frame

            time.sleep(0.01)

        raise TimeoutError(
            f"Timed out waiting for command 0x{command:02X}. "
            f"Saw {len(last_seen_frames)} Eventide SysEx frames during wait."
        )
