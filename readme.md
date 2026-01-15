The goal of the project is to have a minimal USB controller for the Eventide H9 FX unit.

I want to jump to specific presets.
When I want an echo, I might want to jump from 1/1th to 1/8th note echo. Without the steps like 1/2th tripplet in between of the origianl app.

## Documenation
- H9 sysex messages: documentation/Eventide h9 141309_MIDISysexMessages.pdf
- H9 Algorithm Guide: documentation/H9AlgorithmGuide+V12.pdf

## Todo
- [x] Integrate live audio BPM detection.
- [x] Display both H9 BPM and calculated BPM from live audio input. Maybe next to each other.
- [x]Add a switch state, either locked BPM or continues live BPM calculation. If   locked, make a function to send the BPM to H9 one time.
- [x] A setting menu, fullscreen. And a way to permanently save settings. Maybe just a json? In current dir or in home dir?
- Control keys, they need to switch preests in normal view, but change settings in the setting menu.
- Double / halv BPM.
- add to config "ignore these notes" 
- view for traktor
- table for min og max value, and what kind of values. See the other repo. 
- implement other bpm detection algorithm. 


## Notes

Effects I want to use:
`DIGDLY, FLTDLY, ROOM, BKHOLE, ULTRA.T, M-FLTR`

## UI Dashboard (Qt / PySide6)

A minimal dashboard intended for a Raspberry Pi touchscreen.

- Run on Windows (dev): `uv run python ui_main.py`


### Keyboard Shortcuts

All keyboard shortcuts are configurable in `config.json`. Default bindings:

- **Preset navigation**: `Left`/`Right` arrows or `P`/`N`
- **Knob adjustment**: `1`/`Q` (DLY-A), `2`/`W` (DLY-B), `3`/`E` (FBK-A), `4`/`R` (FBK-B)
- **BPM control**: `5`/`T` (+/- BPM), `D` (sync live BPM)
- **Settings**: `S`


### GPIO Hardware Buttons (Raspberry Pi)

GPIO button support is available for Raspberry Pi hardware buttons.
