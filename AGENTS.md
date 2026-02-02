# AGENTS

## Purpose
Control the Eventide H9 via MIDI and SysEx. Primary flows:
- Request current program dump and parse to a preset snapshot.
- Read current BPM via system key `sp_tempo` (BPM * 100).

## UV workflow (important)
- Use UV for dependencies and environments.
- Add dependencies with `uv add <package>` (avoid pip).
- Keep dependencies in `pyproject.toml` (no requirements.txt).
- Run `uv sync` after adding dependencies.

## Run
- Start the app with: `uv run python ui_main.py --log-level DEBUG`
- Stop a running instance before starting another.

## Coding notes
- Keep MIDI/Protocol work off the UI thread.
- UI should trigger actions; background worker handles SysEx/Program Change and publishes a single current state snapshot.
- Prefer a small state model (preset snapshot + bpm + connection status).
- Split code into smaller functions; create new files to keep things organized.

## References
- SysEx spec and algorithm guide are in the documentation folder.

# Environment
- On production this code will run on a raspberry pi 5