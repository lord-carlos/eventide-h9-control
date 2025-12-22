"""Simple test utility: connect to an H9, print current preset, then
keep listening and print all incoming MIDI messages.

Usage: python test.py [--log-level LEVEL]
"""
from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from midi import H9Midi
from h9control.logging_setup import configure_logging
from h9control.domain.knob_display import format_knob_value
from h9control.protocol.h9_protocol import H9Protocol
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT
from h9control.protocol.sysex import decode_eventide_sysex, format_sysex_bytes
from h9control.transport.midi_transport import MidiTransport


def _print_preset(logger: logging.Logger, preset: Any) -> None:
    logger.info("Current preset snapshot:")
    logger.info("- preset_number: %s", preset.preset_number)
    logger.info("- category: %s", preset.category)
    logger.info("- effect_index: %s", preset.effect_index)
    logger.info("- dump_format: %s", preset.dump_format)
    logger.info("- effect_number: %s", preset.effect_number)
    if preset.preset_name is not None:
        logger.info("- preset_name: %s", preset.preset_name)
    if preset.algorithm_name is not None:
        logger.info("- algorithm_name: %s", preset.algorithm_name)

    if preset.knobs_by_name is not None:
        logger.info("Knobs:")
        for name, value in preset.knobs_by_name.items():
            pct = (value / MAX_KNOB_VALUE_14BIT) * 100.0
            pretty = format_knob_value(
                algorithm_key=preset.algorithm_key,
                knob_name=name,
                raw_value=value,
            )
            if pretty is not None:
                logger.info("- %s: %d (%.1f%%) -> %s", name, value, pct, pretty.label)
            else:
                logger.info("- %s: %d (%.1f%%)", name, value, pct)
    elif preset.knob_values is not None:
        logger.info("Knobs (unmapped): %s", preset.knob_values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Can also use H9_LOG_LEVEL env var.",
    )
    args = parser.parse_args()

    configure_logging(cli_level=args.log_level)
    logger = logging.getLogger("test")

    midi = H9Midi(device_prefix="H9 Pedal")

    # Call midi.connect() as requested by the user.
    try:
        out_name = midi.connect()
    except Exception as exc:  # pragma: no cover - hardware dependent
        logger.error("Failed to connect to MIDI: %s", exc)
        raise

    logger.info("Connected to MIDI output: %s", out_name)

    transport = MidiTransport(midi)
    h9 = H9Protocol(transport, device_id=1)

    # Request and print the current preset
    try:
        preset = h9.request_current_program(timeout_s=2.0)
        _print_preset(logger, preset)
    except TimeoutError as exc:
        logger.error("Did not receive a program dump in time: %s", exc)

    # Keep the MIDI channel open and print everything we receive
    logger.info("Listening for incoming MIDI messages. Press Ctrl-C to quit.")

    try:  # pragma: no cover - interactive loop
        while True:
            msgs = transport.receive_pending()
            for msg in msgs:
                # For sys-ex messages, try to decode Eventide frames for nicer output
                if getattr(msg, "type", None) == "sysex":
                    frame = decode_eventide_sysex(msg)
                    if frame is not None:
                        payload_hex = format_sysex_bytes(frame.payload)
                        print(f"RX Eventide SysEx: device={frame.device_id} cmd=0x{frame.command:02X} payload={payload_hex}")
                    else:
                        raw_hex = format_sysex_bytes(list(getattr(msg, "data", [])))
                        print(f"RX SysEx (other): {raw_hex}")
                else:
                    # Generic message (note_on, program_change, etc.) — show repr
                    print(f"RX: {msg}")
            time.sleep(0.05)
    except KeyboardInterrupt:
        logger.info("Interrupted by user; closing MIDI connection.")
    finally:
        midi.close()


if __name__ == "__main__":
    main()
