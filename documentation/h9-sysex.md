# Eventide Factor Series / H9 MIDI SysEx Documentation

This document describes the proprietary System Exclusive (SysEx) protocol for the Eventide Factor series (TimeFactor, ModFactor, PitchFactor, Space) and H9 pedals.

## 1. Message Structure
All Eventide proprietary messages follow this basic format:
`0xF0 0x1C 0x70 <id> <message_code> <data_payload> 0xF7`

* [cite_start]**Manufacturer ID:** `0x1C` (Eventide) [cite: 29, 142]
* [cite_start]**Model ID:** `0x70` (Factor/H9 family) [cite: 30, 144]
* [cite_start]**Device ID (`id`):** `0x00` addresses all units; otherwise must match the unit's configured SysEx ID[cite: 31, 32].
* **Nibblization:** For binary data, an 8-bit byte is split into two 4-bit nibbles. [cite_start]The **most significant nibble** is sent first[cite: 35, 36].
* [cite_start]**ASCII Formatting:** For many commands, numbers are sent as ASCII hexadecimal strings without "0x" or "H"[cite: 153].

---

## 2. Message Codes (Commands)

| Code | Name | Description |
| :--- | :--- | :--- |
| `0x00` | `SYSEXC_OK` | [cite_start]Success response to a command [cite: 38-40]. |
| `0x0D` | `SYSEXC_ERROR` | [cite_start]Indicates an error; payload may contain an ASCII error message [cite: 42-45]. |
| `0x15` | `SYSEXC_PROGRAM_DUMP` | [cite_start]Binary program/preset dump[cite: 47, 48]. |
| `0x2D` | `SYSEXC_VALUE_PUT` | [cite_start]Write a new value for a parameter (Key/Value pair) [cite: 49-52]. |
| `0x2E` | `SYSEXC_VALUE_DUMP` | [cite_start]Response containing a parameter value in ASCII hex[cite: 54, 55]. |
| `0x31` | `SYSEXC_OBJECTINFO_WANT` | [cite_start]Request info for a specific key[cite: 61, 62]. |
| `0x3B` | `SYSEXC_VALUE_WANT` | [cite_start]Request the value for a specific key[cite: 63, 64]. |
| `0x48` | `SYSEXC_TJ_PRESETS_WANT` | [cite_start]Request a dump of all presets in the unit[cite: 66]. |
| `0x49` | `SYSEXC_TJ_PRESETS_DUMP` | [cite_start]Response containing all presets[cite: 69]. |
| `0x4C` | `SYSEXC_TJ_SYSVARS_WANT` | [cite_start]Request a dump of all system variables[cite: 71]. |
| `0x4E` | `SYSEXC_TJ_PROGRAM_WANT` | [cite_start]Request a dump of the currently loaded preset[cite: 77]. |
| `0x50` | `SYSEXC_TJ_ALL_WANT` | [cite_start]Request entire unit state (System Vars + Presets)[cite: 82]. |

---

## 3. Appendix A: Preset Dump Format
[cite_start]A preset dump consists of multiple fields separated by spaces across several lines [cite: 96-98]:

* [cite_start]**Header:** Preset number, Algorithm number, and Dump format number (typically `2`) [cite: 101-103].
* [cite_start]**Knobs:** Effect number (0-9) followed by **10 knob values** in hex (0 to `7FE0`)[cite: 105, 106].
    * [cite_start]*Order:* Bottom left knob (`XKnob/D-Mod`) to top left knob (`Mix/Intensity`)[cite: 106].
* [cite_start]**Pedal:** Expression pedal value in hex (0 to `7FE0`)[cite: 107].
* [cite_start]**Tempo:** Values for `tempo * 100` and a boolean (1/0) for Tempo On/Off[cite: 115, 116].
* [cite_start]**Integrity:** A checksum string (e.g., `C_1322`) representing the integer sum of all values [cite: 121-124].

---

## 4. Appendix B: System Variable Keys
Keys are determined by adding a parameter offset to a base **Type Key Value**.

### Key Bases
* [cite_start]**Boolean (0 or 1):** `0x100` [cite: 239]
* [cite_start]**Byte (0 to 0xFF):** `0x200` [cite: 300]
* [cite_start]**Word (0 to 0xFFFF):** `0x300` [cite: 385]

### Notable Boolean Parameters (Base 0x100)
| Offset | Key Name | Function |
| :--- | :--- | :--- |
| `2` | `sp_bypass` | [cite_start]Sets bypass state[cite: 245, 246]. |
| `7` | `sp_tap_syn` | [cite_start]Enables/Disables Tempo[cite: 255, 256]. |
| `10` | `sp_midiclock_enable` | [cite_start]Enables MIDI clock sync[cite: 260, 261]. |
| `13` | `sp_global_mix` | [cite_start]Read MIX from system[cite: 268]. |
| `21` | `sp_bluetooth_disabled` | [cite_start]1: Disabled, 0: Enabled (H9 only)[cite: 276, 291]. |

### Notable Byte Parameters (Base 0x200)
| Offset | Key Name | Function |
| :--- | :--- | :--- |
| `0` | `sp_bypass_mode` | [cite_start]0:DSP, 1:Relay, 2:DSP+Delay, 3:Mute [cite: 301-305]. |
| `3` | `sp_midi_rx_channel` | [cite_start]MIDI receive channel (0-15)[cite: 315, 322]. |
| `92` | `sp_routing_mode` | [cite_start]0:Normal, 1:4-wire, 2:Wet/Dry[cite: 384, 385]. |

### Notable Word Parameters (Base 0x300)
| Offset | Key Name | Function |
| :--- | :--- | :--- |
| `1` | `sp_mix_knob` | [cite_start]Word value for mix[cite: 385]. |
| `2` | `sp_tempo` | [cite_start]Word value for tempo[cite: 385]. |
| `43` | `sp_input_gain` | [cite_start]Gain in 0.1 dB steps[cite: 387]. |