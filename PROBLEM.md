# Bugs, Errors, and Improvements — Full Analysis

A comprehensive review of the Eventide H9 control codebase, organized by severity.

---

## A. Critical Bugs (broken behavior)

### A1. Duplicate `TREMLO` key in `ALGO_MAP` silently overwrites ModFactor entry
`h9control/domain/algorithms.py:117` and `:224` both define `"TREMLO"` as a dict key. Python dict literals silently keep the **last** definition, so the ModFactor Tremolo (`display_names=["TREMLO", "TREMOLOPAN"]`, knobs=`[..., WIDTH, SHAPE, SPEED, DEPTH, TYPE, INTENS]`) is **completely overwritten** by the Space Tremoverb (`display_names=["TREMLO", "TREMOLOVERB"]`, knobs=`[HIFREQ, STDPTH, SPEED, SHAPE, ...]`).

Consequences:
- `knob_names("TREMLO")` returns the Space knobs for *both* categories.
- `resolve_key_from_category_index(2, 7)` (ModFactor index 7) returns `"TREMLO"`, then `knob_names` returns the **wrong** 10 knob names.
- Any ModFactor Tremolo preset will display/adjust the wrong knobs.
- `resolve_key_from_display_name("TREMOLOPAN")` (the ModFactor display name) will never match, because the entry that contained `"TREMOLOPAN"` in `display_names` was overwritten.

Fix: rename one key (e.g. `"TREMLO_MOD"` vs `"TREMLO_SPC"`) and update the `CATEGORIES` keys list accordingly, or restructure to a nested `(category, key)` scheme.

### A2. Raw knob value never displayed — wrong attribute name
`h9control/app/ui/qt_dashboard.py:523`:
```python
raw_value = getattr(knob, "value", None)
```
But `KnobBarState` (`h9control/app/state.py:10`) defines `raw_value: int`, not `value`. `getattr(knob, "value", None)` **always returns `None`**, so `_raw_value.setText("")` always runs — the raw value label under each progress bar is permanently blank.

Fix: `raw_value = getattr(knob, "raw_value", None)`.

### A3. GPIO pin conflict in `config.json` — `sync_live_bpm` and `encoder_1_push` both on pin 23
`config.json:73` (`sync_live_bpm` pin 23) and `:91` (`encoder_1_push` pin 23). In `qt_worker.py:_setup_gpio_bindings`, the `pin_actions` dict is keyed by pin and only has `tap`/`hold` slots — a second non-hold action on the same pin **silently overwrites** the first. So `sync_live_bpm` is lost and `encoder_1_push` (the modifier for knob 1's encoder) wins. No warning is logged.

Fix: either correct `config.json` (move one button to a free pin) or make `_setup_gpio_bindings` detect same-pin conflicts and refuse/error.

### A4. Manual BPM adjust is overridden by continuous auto-sync
`qt_worker.py:541 adjust_bpm` does not update `self._last_sent_auto_bpm`. Only `sync_live_bpm` (line 578) and `_check_auto_bpm_sync` (line 617) set it. In continuous mode, `_emit_state` (line 826) calls `_check_auto_bpm_sync` on every state publication. So immediately after a manual `adjust_bpm`, the next `_emit_state` sees `rounded_bpm != last_sent_auto_bpm` and **re-sends the live BPM**, clobbering the user's manual value.

Fix: in `adjust_bpm`, set `self._last_sent_auto_bpm = target` after `set_bpm`, or add a "manual override cooldown" that suppresses auto-sync for a few seconds after a manual change.

### A5. `bpm_detected.emit(-1.0)` (stream death) is not handled by the worker
`beat_detector.py:348/364/374` emit `-1.0` as an error sentinel. `qt_worker.py:833 update_live_bpm` sets `self._live_bpm = -1.0` with no special handling. Then:
- `_state_with_overrides` puts `live_bpm=-1.0` into `DashboardState`.
- The dashboard renders `"-1.0 Live"`.
- `_sanitize_bpm` only guards the *device* BPM, not the live BPM.
- In continuous mode, `_check_auto_bpm_sync` would clamp `-1` to `20` and try to send BPM 20 to the pedal.

Fix: in `update_live_bpm`, treat `bpm <= 0` as "unavailable" and set `self._live_bpm = None`.

---

## B. Notable Bugs (incorrect in specific scenarios)

### B1. `BUFFER_DURATION` and `START_BPM` defined twice in `beat_detector.py`
Lines 28 & 57 both define `BUFFER_DURATION` (8.0 then 10.0 — the 10.0 wins). Lines 33 & 53 both define `START_BPM` (both 120.0, harmless but confusing). The module-level comment at line 28 says "8.0 seconds" but the actual value used is 10.0.

### B2. `format_knob_value` applies FILTER/SPEED/FEEDBK scaling to *all* algorithms
`knob_display.py:187-206` applies `FEEDBK * 1.1` (110%), `FILTER * 2 - 100` (±100), `SPEED * 5.01/100` (0–5.01 Hz) to every algorithm that has a knob of that name. But the H9 algorithm guide shows different ranges for some algorithms (e.g. `UNDLTR.FEEDBK` may not go to 110%, `ROTARY.HRNSPD` is not 0–5 Hz). The TimeFactor-specific DLY-A/DLY-B branch at line 180 is correctly scoped, but the FILTER/SPEED/FEEDBK branches are not.

### B3. `knob_names.reverse()` in `_adjust_single_knob` is undocumented and fragile
`qt_worker.py:506` reverses the knob list from `ALGO_MAP` before computing the CC number. The ALGO_MAP order is "bottom-left to top-left" per the spec (FILTER first → CC 22), but the empirical CC mapping (verified by `scripts/test_delay_cc.py` which uses CC 24 for DLY-A) shows DLY-A = CC 24 = knob 3, which only matches **after** reversal. So the reversal is currently correct for TimeFactor, but:
- It's not documented why the reversal is needed.
- The comment at line 500 ("Knob 1 = CC 22, Knob 2 = CC 23, …, Knob 10 = CC 31") is inconsistent with the reversal.
- If `ALGO_MAP` is ever reordered to match CC order directly, the reversal will silently start sending the wrong CCs.

### B4. Knob adjust sends 7-bit CC but stores 14-bit override
`qt_worker.py:515` converts `new_raw` (0..0x7FE0, 14-bit) to `cc_value` (0..127, 7-bit) and sends the CC, but stores the 14-bit `new_raw` in `_knob_overrides` (line 538). The H9's actual 14-bit value after receiving the CC won't match the stored override, so:
- The UI shows a 14-bit value that doesn't reflect the device's real state.
- On the next refresh, the override (14-bit) is used instead of the device's actual value, so the discrepancy persists until the preset changes.
- The H9 supports 14-bit CC via CC pairs (MSB + LSB) for high-resolution parameter control — the code only sends the MSB.

### B5. `H9Backend.set_knob_value` / `knob_key` are misleading
`h9_backend.py:33-56` claims to set knobs via the byte-parameter key scheme (`0x200 + offset`), but H9 knob values are 14-bit (0..0x7FE0), not byte (0..0xFF). The `set_knob_value` validator restricts to `0..0xFF` (line 53), so it can't reach the upper range. This method is currently dead code (the worker uses CC instead), but it's a trap for future callers.

### B6. `H9Midi.connect()` leaks ports on re-connect
`midi.py:50-72` overwrites `self._out` and `self._in` without closing the previous ones. If `connect()` is called twice (e.g. via `MidiTransport.connect()`), the old ports remain open. On Windows/RTMafi this can lock the port so no other process can open it.

### B7. `shutdown()` doesn't stop the event-refresh QTimer
`qt_worker.py:641-658` stops the RX thread and closes the transport, but never calls `self._event_refresh_timer.stop()`. If a preset-change detection fires during shutdown, the timer could fire on a destroyed transport.

### B8. `H9Protocol` RX loop and `H9DeviceWorker` RX loop both call `receive_pending`
`H9Protocol._wait_for_value_dump` / `_wait_for_command` poll `transport.receive_pending()` themselves, while the worker's `_rx_loop` also polls the same queue. If both run at once they compete for messages. Currently they're never used together (UI uses `H9Backend` + `_FrameWaiter`; CLI uses `H9Protocol`), but the architecture is fragile — `H9Protocol` is essentially duplicated logic that should delegate to the worker's frame-waiter.

### B9. `preset.py` parsing of trailing names is heuristic and order-dependent
`preset.py:134-144` assumes trailing lines after `C_` are `<ALGO NAME>` then `<PRESET NAME>`. The spec doesn't clearly state the order, and the single-line fallback (line 140-144) guesses based on whether the line resolves as an algorithm display name. If a user names a preset "HALL" (a known algorithm name), the parser will treat it as the algorithm name and leave `preset_name = None`.

### B10. `_extract_stereo_channels_fast` doesn't validate `selected_channels` length
`beat_detector.py:186-187` indexes `self.selected_channels[0]` and `[1]` without checking length. The guard at line 225 only runs in `_start_stream` (and only when `not mono_mode`), but `_extract_stereo_channels_fast` is also called from `_audio_callback` which runs on the audio thread. If config is mutated to a 1-element list mid-stream, this raises IndexError inside the audio callback (which sounddevice swallows, but the stream may stop).

### B11. `knob_display.format_knob_value` for `DLY-A`/`DLY-B` ignores `algorithm_key`
`knob_display.py:180` applies the TimeDivision quantization to `DLY-A`/`DLY-B` for **all** algorithms, not just `_TIMEFACTOR_ALGO_KEYS`. `SPCTME` and `PTCFUZ` also have `DLY-A`/`DLY-B` knobs, but they may not be tempo-synced note divisions. The check should be `if algo in _TIMEFACTOR_ALGO_KEYS and name in {"DLY-A", "DLY-B"}`.

### B12. `algorithms.py` — `FLTDLY` has only `FBK-A` (no `FBK-B`)
Line 274: `["FILTER", "SPEED", "DEPTH", "SHAPE", "SLUR", "FBK-A", "DLY-B", "DLY-A", "DLYMIX", "MIX"]` — 9 unique + FBK-A. Other TimeFactor algorithms have both FBK-A and FBK-B. If this is correct per the H9 spec, fine — but if `lock_feedback` is enabled and the user adjusts `FBK-A`, `adjust_knob` (line 429) tries to also adjust `FBK-B` which doesn't exist. The `_adjust_single_knob` fallback handles this (line 478), but it logs a warning every time — noisy.

---

## C. Dead Code / Redundancy

### C1. `_invoke_on_main_thread` is never called
`qt_worker.py:335-354` — defined but no caller anywhere in the codebase. GPIO callbacks use Qt signals instead. Remove.

### C2. `H9Protocol.set_parameter` is never called
`h9_protocol.py:60-81` — the worker uses `H9Backend.set_value`. Remove or wire it up.

### C3. `H9Backend.set_knob_value` / `knob_key` are never called
`h9_backend.py:33-56` — the worker uses MIDI CC. Either remove or actually use it (and fix the 14-bit-vs-byte bug, B5).

### C4. Duplicate `from h9control.app.config import ConfigManager` in `qt_dashboard.py`
Imported at line 8 (top) and again at line 528 (bottom). The bottom import is a leftover from a circular-import workaround that's no longer needed.

### C5. `SettingsWidget.__del__` is a no-op
`qt_settings.py:578-580` — `def __del__(self): pass`. Remove (and avoid `__del__` on Qt objects in general; deletion order is unreliable).

### C6. `BeatDetector.__del__` is a no-op
`beat_detector.py:667-669` — same. Remove.

### C7. `_pct_from_midi_cc` is unused
`knob_display.py:109-111` — defined but never called.

---

## D. Concurrency / Thread-Safety Issues

### D1. `BeatDetector` ring buffer is not actually lock-free
The class docstring says "lock-free ring buffer" and "eliminates GIL contention", but `_audio_callback` (line 150) and `_read_ring_buffer` (line 409) both take `self._write_lock`. A lock inside a real-time audio callback can cause xruns on a lightly-loaded Pi, and is a known anti-pattern. Either use a true single-producer/single-consumer ring buffer (atomic indices) or remove the misleading docstring.

### D2. `_read_ring_buffer` reads `self.write_index` outside the lock
Line 413 reads `self.write_index` without holding `_write_lock`, while the callback writes it under the lock. The GIL makes the int read atomic, but the **consistency** between `write_index` and `ring_buffer` contents isn't guaranteed — you can read a `write_index` that points past data the callback hasn't finished writing yet.

### D3. `time.sleep(0.3)` in `_change_preset` and `jump_to_preset` blocks the worker thread
`qt_worker.py:385` and `:799`. During the 300ms wait, the worker can't process any other queued signals (knob tweaks, BPM adjust, etc.). Use a `QTimer.singleShot(300, self._refresh_state)` instead, or track pending operations.

### D4. `thread.wait(2000)` in `app.aboutToQuit` blocks the UI on exit
`ui_main.py:128` — blocks the main thread up to 2s during shutdown. If the worker is mid-`time.sleep(0.3)` it could actually take the full 2s.

### D5. `_rx_loop` busy-waits with 5ms sleep
`qt_worker.py:919` — `time.sleep(0.005)` in a tight loop. `mido` supports blocking `receive()` with timeout; use it instead of polling.

---

## E. Configuration / UX Issues

### E1. `ShortcutsConfig.default()` omits `next_preset`, `prev_preset`, `connect_refresh`, `jump_to_preset_*`
`config.py:61-77` only defines knob/BPM/settings/sync keys. A fresh install with no `config.json` has **no** preset navigation shortcuts at all. The user's `config.json` has them, but defaults should be usable.

### E2. `ConfigManager.load()` swallows all errors and returns defaults
`config.py:174-176` — any exception returns `AppConfig.default()` with only an `error` log. A user with a corrupted `config.json` silently loses all settings. Should surface the error to the UI.

### E3. Every property setter calls `save()` → many disk writes
`config.py` — each setter (`lock_delay =`, `theme_mode =`, etc.) writes the entire config to disk. Toggling 4 checkboxes writes the file 4 times. On a Pi SD card, this is wear-and-tear. Debounce or batch saves.

### E4. Brightness slider writes to sysfs on every tick
`qt_settings.py:331` — `valueChanged` fires repeatedly while dragging. `backlight.set_brightness_percent` writes to `/sys/class/backlight/.../brightness` on every tick. Debounce with a ~200ms timer.

### E5. BPM button click triggers refresh, not BPM adjust
`qt_dashboard.py:253` — `self._btn_bpm.clicked.connect(self.connect_refresh_requested.emit)`. A user clicking the BPM number expects to adjust BPM, not refresh the whole state. Confusing.

### E6. Audio device list is never refreshed
`qt_settings.py:_populate_devices` runs once at init. Hot-plugged USB audio devices won't appear without restarting the app.

### E7. Channel selection allows identical L/R channels
`qt_settings.py:_on_channel_changed` doesn't reject `left_channel == right_channel`. The beat detector would then get the same audio on both "stereo" channels (degraded beat detection, not a crash).

### E8. `fit_window_to_screen` doesn't center the window
`qt_dashboard.py:563-574` resizes but doesn't reposition. The window appears in the top-left corner.

### E9. Status dot as settings button has no tooltip
`qt_dashboard.py:212-227` — a clickable dot that opens settings, but no tooltip text. Discoverable only by accident.

### E10. `backlight.py` writes to sysfs without trailing newline
Line 79: `brightness_file.write_text(str(value))`. Some kernel drivers expect a newline; `write_text(f"{value}\n")` is safer.

---

## F. Validation / Robustness Gaps

### F1. `preset.py` doesn't validate knob value range
Line 119: `int(tok, 16)` accepts any hex. Bad data (>0x7FE0) flows into the UI as >100% percentages. Clamp or warn.

### F2. `preset.py` drops knobs silently if <10 values
Line 123: `if len(hex_values) >= 10:` — if a dump has 9 values, `knob_values` stays `None` and `knobs_by_name` is never built. The dashboard shows zero knobs with no warning.

### F3. `sysex.build_eventide_sysex` doesn't validate payload bytes are 7-bit
`sysex.py:72-84` — payload bytes >127 produce malformed SysEx. Validate `0 <= b <= 0x7F`.

### F4. `h9_backend.get_value` decimal/hex heuristic fails on negative hex
`h9_backend.py:85-87` — `value_part.lstrip("-").isdigit()` then `int(value_part, 10)`, else `int(value_part, 16)`. A value like `"-A0"` fails the first branch and raises in the second. Unlikely from the H9 but not impossible.

### F5. No automated tests
No `tests/` directory, no `pytest` config, no `test_*.py` outside `scripts/`. The `test.py` at the root is a manual hardware-integration script. The protocol parser (`preset.py`), algorithm resolver, and knob display formatter are pure functions and very testable — they should have unit tests with captured real dump texts.

### F6. `pyproject.toml` has no dev/test dependencies section
No `[project.optional-dependencies]` with `pytest`, `ruff`, etc. `ruff` is clearly used (there's a `.ruff_cache/`) but isn't declared.

### F7. `gpiozero` is an unconditional dependency
`pyproject.toml:8` — `gpiozero>=2.0.1` installs on Windows/macOS too, where it can't work. Add a platform marker (`platform_machine == "aarch64"` or `sys_platform == 'linux'`) like `lgpio` already does.

---

## G. Minor / Style

- **G1.** `qt_dashboard.py:528-529` imports at the bottom of the file (circular-import workaround). `ConfigManager` is already imported at the top (line 8) — redundant.
- **G2.** `test.py:96-102` uses `print()` for RX messages while the rest uses `logger`. Inconsistent.
- **G3.** `midi_transport.py:68,89` reaches into `H9Midi._out` (private). Add public `send_program_change`/`send_control_change` to `H9Midi`.
- **G4.** `qt_dashboard.py:262-264` overrides `mousePressEvent` on a `QLabel` without calling super or accepting the event. Works but is non-idiomatic; use a clickable `QPushButton` with flat styling instead.
- **G5.** `h9_protocol.py` and `h9_backend.py` duplicate the value-decode heuristic. Consolidate into one helper.
- **G6.** `qt_settings.py:549` `state == QtCore.Qt.CheckState.Checked.value` — compares int to enum value. Works but `Qt.CheckState(state) == Qt.CheckState.Checked` is clearer.
- **G7.** `beat_detector.py:134` logs `status.input_overflow` as "Total:" — it's a bool, not a count.
- **G8.** `qt_dashboard.py:436-445` `make_multi_handler` closure is correct but the loop-bound `handlers` variable is the kind of thing that easily breaks in future edits; a comment would help.
- **G9.** `ui_main.py:136` `raise SystemExit(app.exec())` — unusual; `sys.exit(app.exec())` is conventional.
- **G10.** `logging_setup.py` is called `logging_setup.py` but the AGENTS.md run command says `--log-level DEBUG` works — confirmed it does. No issue, just noting it's wired correctly.

---

## Recommended Fix Priority

| Priority | Item | Effort |
|---|---|---|
| P0 | A1 duplicate `TREMLO` | Small — rename one key + update CATEGORIES |
| P0 | A2 `getattr(knob, "value")` → `"raw_value"` | Trivial |
| P0 | A3 pin 23 conflict in config.json | Trivial (config) + small (add conflict detection) |
| P0 | A4 manual BPM overridden by auto-sync | Small — set `last_sent_auto_bpm` in `adjust_bpm` |
| P0 | A5 `-1.0` live BPM not handled | Small — guard in `update_live_bpm` |
| P1 | B1 duplicate `BUFFER_DURATION` | Trivial |
| P1 | B11 `DLY-A/B` quantization not algo-scoped | Small |
| P1 | B4 14-bit override vs 7-bit CC mismatch | Medium — use CC14 pairs or store 7-bit |
| P1 | B7 timer not stopped in shutdown | Trivial |
| P1 | F3 SysEx payload byte validation | Small |
| P2 | B6 `connect()` port leak | Small |
| P2 | B9 preset name parsing heuristic | Medium — needs real dump samples |
| P2 | D1/D2 ring buffer locking | Medium — real SPSC ring or fix docstring |
| P2 | E3/E4 debounce config/brightness saves | Small |
| P3 | F5 add unit tests for `preset.py`, `algorithms.py`, `knob_display.py` | Medium |
| P3 | C1–C7 dead code removal | Trivial |
