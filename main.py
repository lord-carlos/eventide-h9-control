import argparse
import logging
import time

from midi import H9Midi

from h9control.logging_setup import configure_logging
from h9control.domain.knob_display import format_knob_value
from h9control.protocol.h9_protocol import H9Protocol
from h9control.protocol.codes import MAX_KNOB_VALUE_14BIT
from h9control.transport.midi_transport import MidiTransport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR). Can also use H9_LOG_LEVEL env var.",
    )
    parser.add_argument(
        "--next-preset",
        action="store_true",
        help="Send a MIDI Program Change to move to the next preset, then re-read current program.",
    )
    parser.add_argument(
        "--midi-channel",
        type=int,
        default=0,
        help="MIDI channel for Program Change (0-15). Default: 0.",
    )
    parser.add_argument(
        "--print-bpm",
        action="store_true",
        help="Request and print the current BPM from the unit.",
    )
    args = parser.parse_args()

    configure_logging(cli_level=args.log_level)
    logger = logging.getLogger("main")

    midi = H9Midi(device_prefix="H9 Pedal")
    ports = midi.list_ports()
    logger.info("Available MIDI outputs:")
    for name in ports.outputs:
        logger.info("- %s", name)

    output_name = midi.connect()
    logger.info("Connected to: %s", output_name)

    transport = MidiTransport(midi)
    h9 = H9Protocol(transport, device_id=1)

    try:
        if args.print_bpm:
            bpm = h9.get_current_bpm(timeout_s=2.0)
            logger.info("Current BPM: %.2f", bpm)

        preset = h9.request_current_program(timeout_s=2.0)
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

        if args.next_preset:
            current_program = 0
            if preset.preset_number is not None:
                # Program Change is 0-based; our dump preset_number appears 1-based.
                current_program = max(0, preset.preset_number - 1)

            next_program = (current_program + 1) % 128
            logger.info(
                "Switching to next preset via Program Change: channel=%d program=%d",
                args.midi_channel,
                next_program,
            )
            transport.send_program_change(program=next_program, channel=args.midi_channel)

            # Give the pedal a moment to switch before requesting the dump again.
            time.sleep(0.3)

            preset2 = h9.request_current_program(timeout_s=2.0)
            logger.info("After Program Change:")
            logger.info("- preset_number: %s", preset2.preset_number)
            logger.info("- category: %s", preset2.category)
            logger.info("- effect_index: %s", preset2.effect_index)
            logger.info("- dump_format: %s", preset2.dump_format)
            logger.info("- effect_number: %s", preset2.effect_number)
            if preset2.preset_name is not None:
                logger.info("- preset_name: %s", preset2.preset_name)
            if preset2.algorithm_name is not None:
                logger.info("- algorithm_name: %s", preset2.algorithm_name)
            if preset2.knobs_by_name is not None:
                logger.info("Knobs:")
                for name, value in preset2.knobs_by_name.items():
                    pct = (value / MAX_KNOB_VALUE_14BIT) * 100.0
                    pretty = format_knob_value(
                        algorithm_key=preset2.algorithm_key,
                        knob_name=name,
                        raw_value=value,
                    )
                    if pretty is not None:
                        logger.info("- %s: %d (%.1f%%) -> %s", name, value, pct, pretty.label)
                    else:
                        logger.info("- %s: %d (%.1f%%)", name, value, pct)
            elif preset2.knob_values is not None:
                logger.info("Knobs (unmapped): %s", preset2.knob_values)
    except TimeoutError as exc:
        logger.error("Did not receive a program dump in time: %s", exc)


if __name__ == "__main__":
    main()
