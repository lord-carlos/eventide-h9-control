This project controls the Eventide H9 MIDI FX unit via MIDI SysEx (Eventide manufacturer 0x1C, model 0x70).

Primary flows implemented:
- Read current preset/program dump (SYSEXC_TJ_PROGRAM_WANT 0x4E -> PROGRAM_DUMP 0x4F) and parse to a `PresetSnapshot`.
- Read current BPM via system variable `sp_tempo` (key 0x302) using VALUE_WANT (0x3B) -> VALUE_DUMP (0x2E). Tempo is BPM*100.

## Where to Find What
- CLI entrypoint: `main.py` (connects to “H9 Pedal…”, optional `--print-bpm`, prints parsed preset + knobs)
- MIDI I/O: `midi.py` (port discovery + connect) and `h9control/transport/midi_transport.py` (send/receive + Program Change)
- SysEx framing/parsing: `h9control/protocol/sysex.py`
- High-level device operations: `h9control/protocol/h9_protocol.py`
	- `request_current_program()` (0x4E/0x4F)
	- `get_current_bpm()` / `get_value()` (0x3B/0x2E)
- SysEx codes + system keys: `h9control/protocol/codes.py` (includes `H9SystemKeys.KEY_SP_TEMPO = 0x302`)
- Program dump parsing + snapshot model: `h9control/domain/preset.py`
- Algorithm/category mapping + knob names: `h9control/domain/algorithms.py`
- Human-friendly knob formatting (TimeFactor note divisions, DLYMIX A/B): `h9control/domain/knob_display.py`

## Documentation
- H9 SysEx spec: `documentation/Eventide h9 141309_MIDISysexMessages.pdf`
- H9 Algorithm Guide: `documentation/H9AlgorithmGuide+V12.pdf`
- Local notes/summaries: `documentation/h9-sysex.md`

## UI Ideas (MVP)
- Goal: a simple “pedal dashboard” showing current preset, algorithm, BPM, some knobs (with TimeFactor-friendly formatting where available), plus buttons for next/prev preset and tap/tempo sync later.
- Resolution (hardcoded for now): start with `720×1280` fullscreen. Keep this as a single constant so it’s easy to change later.

## Compile and run
Compile and run the app with `uv run python ui_main.py --log-level DEBUG`

Architecture notes:
- Keep `H9Protocol` + `MidiTransport` off the UI thread. UI triggers actions; a background worker performs SysEx/Program Change and publishes a single “current state” snapshot back to the UI.
- Prefer a small state model (preset snapshot + bpm + connection status). UI renders from state; don’t let widgets call MIDI directly.