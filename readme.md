The goal of the project is to have a minimal USB controller for the Eventide H9 FX unit.

I want to jump to specific presets.
When I want an echo, I might want to jump from 1/1th to 1/8th note echo. Without the steps like 1/2th tripplet in between of the origianl app.

## Documenation
- H9 sysex messages: documentation/Eventide h9 141309_MIDISysexMessages.pdf
- H9 Algorithm Guide: documentation/H9AlgorithmGuide+V12.pdf

## Todo
- [x] Integrate live audio BPM detection.
- [x] Display both H9 BPM and calculated BPM from live audio input. Maybe next to each other.
- Add a switch state, either locked BPM or continues live BPM calculation. If   locked, make a function to send the BPM to H9 one time.
- A setting menu, fullscreen. And a way to permanently save settings. Maybe just a json? In current dir or in home dir?
- Control keys, they need to switch preests in normal view, but change settings in the setting menu.
- Double / halv BPM.


## Notes

Traktor delay rates:
 - 4/4, 3/8, 1/4, 3/16, 1/8, 1/16, 1/32

## UI Dashboard (Qt / PySide6)

A minimal dashboard intended for a Raspberry Pi touchscreen.

- Run on Windows (dev): `uv run python ui_main.py`

Note: currently starts windowed at 720×1280 for debugging.

Controls:
- `Connect / Refresh` fetches preset + BPM.
- `◀` / `▶` sends Program Change (prev/next) and re-reads state.