# Open Issues & Cleanups — Work Segments

*Reviewed 2026-10-28 against commit `a70f3bd`. Line numbers verified at review time and
will drift after edits — re-locate by code context, not line number.*

## How to use this document

The work is split into **segments that do not touch each other**. Each segment
lists exactly which files it is allowed to modify. Two agents / developers can
work on different segments in parallel without merge conflicts.

Rules:

- Stay inside your segment's **Files** list. If a fix seems to require touching
  a file owned by another segment, stop and note it in your PR instead.
- **Out of scope** (per project owner):
  - `config.json` GPIO pin assignments — existing values are examples, not bugs.
    Any workaround for real hardware belongs in the owner's live config.
  - `/scripts/` directory — leave alone for now (this includes its imports of
    the root `midi.py`, see Segment 2's compat-shim note).
- Suggested order *within* a segment follows the numbering, but everything in
  a segment is independent of other segments.

Cross-segment coordination is only needed in two cases, both explicitly handled
inside the segment itself: importing the moved `midi.py` (Segment 2 uses a
compat shim to avoid conflicting with Segment 1) and the `qt_worker.py` split
(Segment 1 owns it; nobody else creates files in `h9control/app/ui/`).

---

## Segment 1 — Worker (qt_worker.py) bugs + split

**Files:** `h9control/app/ui/qt_worker.py`, new modules under `h9control/app/` (e.g. `bpm_sync.py`, `gpio_binding.py`, `knob_override.py`) if splitting.

The file is ~875 lines doing GPIO binding, BPM sync logic, knob overrides, the
RX loop and state snapshotting in one class. AGENTS.md asks for smaller
files/functions. Natural seams:

- GPIO shortcut/encoder binding → own module
- BPM logic (`adjust_bpm`, `sync_live_bpm`, `_check_auto_bpm_sync`) → `h9control/app/bpm_sync.py`
- Knob override/CC logic → own module

Do the fixes below first, then the split (or vice versa — but do one commit per
step so the split is reviewable).

### 1.1 Manual BPM adjust is overridden by auto-sync (not fixed)
`adjust_bpm` (~line 555) does **not** update `self._last_sent_auto_bpm`; only
`sync_live_bpm` and `_check_auto_bpm_sync` do. In `auto_bpm_mode="continuous"`,
every state publication calls `_check_auto_bpm_sync`, which immediately re-sends
the live BPM and clobbers the value the user just set.

Fix: in `adjust_bpm`, set `self._last_sent_auto_bpm = target` after `set_bpm`
(the same pattern already used in `sync_live_bpm`), or add a short manual-
override cooldown that suppresses auto-sync for a few seconds.

### 1.2 `-1.0` stream-death sentinel stored as live BPM
`beat_detector` emits `-1.0` when the audio stream dies; `update_live_bpm`
(~line 856) stores it verbatim. Result: dashboard renders "-1.0", and in
continuous mode auto-sync would clamp `-1` → BPM 20 and send it to the pedal.
`_sanitize_bpm` only guards the *device* BPM.

Fix: in `update_live_bpm`, treat `bpm <= 0` as "unavailable" and set
`self._live_bpm = None`.

### 1.3 Preset changes block the worker for 300 ms
`time.sleep(0.3)` in `jump_to_preset` (~line 398) and `_change_preset`
(~line 823). During the wait the worker can't process queued knob tweaks, BPM
adjusts, GPIO events, etc.

Fix: send the program change, then schedule the refresh with
`QTimer.singleShot(300, self._refresh_state)` instead of sleeping.

### 1.4 `shutdown()` doesn't stop the event-refresh timer
`shutdown()` (~line 655) joins the RX thread and closes the transport but never
calls `self._event_refresh_timer.stop()`. The timer (~line 133) can then fire
`_refresh_after_event` against a destroyed transport.

Fix: stop the timer first thing in `shutdown()`.

### 1.5 Metrology notes worth fixing while refactoring (small)
- `_rx_loop` (~line 919) busy-waits with `time.sleep(0.005)` polling.
  mido/rtmidi supports blocking receive with timeout — prefer that.
- `knob_names.reverse()` (~line 520) before computing the CC number is
  undocumented and fragile; the comment above it ("Knob 1 = CC 22 …") contradicts
  the reversal.
  Document *why* the ALGO_MAP order must be reversed to match CC numbers
  (empirically DLY-A = CC 24, cf. `scripts/test_delay_cc.py`), and reword the
  comment accordingly.
- `_invoke_on_main_thread` (~lines 348–367) is dead code (GPIO callbacks use Qt
  signals). Delete it.

### 1.6 7-bit CC sent, 14-bit override stored
Knob adjust converts `new_raw` (14-bit, 0..0x7FE0) to a 7-bit CC value
(~line 529) and sends the CC, but stores the full 14-bit `new_raw` in
`_knob_overrides` (~line 552). The H9's real 14-bit value after receiving a
7-bit CC won't match the stored override, so the UI shows a value that the
device doesn't have and the discrepancy persists until the next preset change.
The override should be derived from what was actually sent (or quantize the
override to the 7-bit grid), or the knob should be marked "pending device
refresh" instead of storing a value.

---

## Segment 2 — MIDI transport (midi.py / H9Midi)

**Files:** `midi.py`, `h9control/transport/midi_transport.py`, and (import-only) `h9control/app/ui/qt_worker.py` line `from midi import H9Midi`. *No* other files.

### 2.1 `connect()` leaks open ports on re-connect
`H9Midi.connect()` (`midi.py` ~lines 50–72) overwrites `self._out` / `self._in`
without closing previous handles. Calling `connect()` twice leaves orphaned
open MIDI ports; on Windows/rtmidi this can lock the device so no other process
can open it.

Fix: close existing handles at the top of `connect()` (the logic already exists
in `close()`).

### 2.2 `midi_transport.py` reaches into `H9Midi` private attributes
`midi_transport.py` uses `getattr(self._midi, "_out", None)` (~lines 82, 103)
instead of a public API. Add public `send_program_change` / `send_control_change`
methods to `H9Midi` and use them. This pairs well with 2.1 and only changes the
two files owned by this segment.

### 2.3 `midi.py` lives outside the `h9control` package (layout inversion, optional)
`H9Midi` is core infrastructure but sits at the repo root, imported *into* the
package from `h9control/transport/midi_transport.py` and `qt_worker.py`.

Suggested approach that keeps this segment conflict-free with Segment 1 and
with `scripts/` (which is out of scope):

1. Create `h9control/transport/h9_midi.py` containing the current `midi.py`.
2. Replace root `midi.py` with a 3-line compat shim: `from h9control.transport.h9_midi import *  # noqa` (keeps `scripts/` untouched).
3. Update the two in-package imports to the new location.

---

## Segment 3 — Audio / beat detection

**Files:** `h9control/audio/beat_detector.py` only.

### 3.1 Ring buffer is not actually lock-free (docstring lies + risky pattern)
The module and class docstrings promise a "lock-free ring buffer" that
"eliminates GIL contention", but `_audio_callback`, `_read_ring_buffer`
(~line 447) and one more read path (~line 500) all acquire `self._write_lock`.
A lock inside a real-time audio callback is a known anti-pattern that can cause
xruns on a lightly loaded Pi.

Additionally `_read_ring_buffer` reads `self.write_index` outside the lock
(~line 451) while the callback writes it under the lock — the GIL makes the int
read atomic, but the *consistency* of `write_index` vs buffer contents is not
guaranteed.

Either implement a true single-producer/single-consumer ring (atomically
published index; consumer never writes), or keep the lock and just fix the
misleading docstrings. Fix the `write_index` read either way.

### 3.2 `_extract_stereo_channels_fast` doesn't validate `selected_channels` length
It indexes `self.selected_channels[0]` and `[1]` (~lines 209–210). The values
are clamped, but if the config list itself has fewer than 2 entries mid-stream,
this raises `IndexError` on the audio thread (IndexError inside `sounddevice`
callbacks is swallowed but can kill the stream).

Fix: guard `len(self.selected_channels) < 2` → fall back to mono-duplication path.

---

## Segment 4 — Config layer

**Files:** `h9control/app/config.py` only.

### 4.1 `ShortcutsConfig.default()` omits preset-navigation shortcuts
`default()` (~lines 61–79) only defines knob/BPM/settings keys. A fresh install
without a `config.json` has **no** next/prev preset, connect-refresh or
jump-to-preset shortcuts at all. Defaults should be usable out of the box.

### 4.2 `load()` silently falls back to defaults on any error
The broad `except` (~lines 178–180) logs and returns `AppConfig.default()`.
A corrupted `config.json` silently loses all user settings with no way to
recover from the UI. At minimum: preserve the broken file as a `.bak` and
expose the error (e.g. a `load_error` attribute / signal the settings dialog
can show).

### 4.3 Every property setter writes the whole config to disk
Each of the ~10 setters calls `save()`. Toggling 4 checkboxes = 4 full-file
writes — needless wear on a Pi SD card. Debounce saves (e.g. dirty-flag +
single-shot timer, or an explicit `save()` call at the end of a settings-edit
batch) and only write when content actually changed.

---

## Segment 5 — UI polish (dashboard + settings)

**Files:** `h9control/app/ui/qt_dashboard.py`, `h9control/app/ui/qt_settings.py` only.

### 5.1 BPM tile button opens a full refresh, not BPM adjust
`qt_dashboard.py` ~line 370: `self._btn_bpm.clicked.connect(self.connect_refresh_requested.emit)`.
Users clicking the BPM number expect to adjust BPM (+/− or a small popover),
not to re-query the pedal.

### 5.2 Brightness slider writes sysfs on every tick
`qt_settings.py` ~line 382 connects `valueChanged` directly to
`_on_brightness_changed`, which writes `/sys/class/backlight/.../brightness` on
every pixel of drag. Debounce ~200 ms (single-shot `QTimer`).

### 5.3 `fit_window_to_screen` never centers the window
`qt_dashboard.py` ~lines 756–767 resize only; the window ends up wherever the
WM puts it (top-left). Call `window.move(avail.center() - window.rect().center())`.

### 5.4 Status-dot tooltip shows status text only
`qt_dashboard.py` ~line 637 sets the tooltip to the current status text. The
dot is also the settings button (`.clicked.connect(settings_requested)` at
~line 314) — append a hint like "· click to open settings" to the tooltip.

---

## Segment 6 — Protocol & validation hardening

**Files:** `h9control/protocol/sysex.py`, `h9control/domain/preset.py`, `h9control/protocol/h9_protocol.py`, `h9control/app/h9_backend.py`, `pyproject.toml` (dependency markers). *No* UI files.

### 6.1 `adjust_bpm`-side: unify the value-decode heuristic
`h9_protocol.py` (~lines 156–159) and `h9_backend.py` (~lines 59–61) each
implement their own decimal-vs-hex guess for `VALUE_DUMP` payloads — and they
disagree (regex vs `lstrip`). The backend version crashes on a value like
`-A0` (fails the decimal check, then raises in `int(..., 16)`).
Extract one helper (suggest putting it in `h9control/protocol/sysex.py` or a
new `h9control/protocol/decode.py`) and use it from both. There are existing
tests for both call sites — keep them green.

### 6.2 `build_eventide_sysex` doesn't validate payload bytes are 7-bit
`sysex.py` ~line 72: payload bytes > 0x7F produce a malformed SysEx message.
Add a `0 <= b <= 0x7F` check with a clear error.

### 6.3 `preset.py` range validation + silent knob drop
- Knob hex tokens (`int(tok, 16)` ~line 119) are accepted unvalidated; values
  > 0x7FE0 flow into the UI as >100% bars. Clamp or flag.
- `if len(hex_values) >= 10:` (~line 123) silently produces `knob_values=None`
  for short dumps — the dashboard then shows zero knobs with no warning. Log a
  warning when a dump yields < 10 knob values.

### 6.4 `H9Protocol.set_parameter` has no production caller
Defined (~lines 60–81) and only exercised by unit tests. Either wire it into
the backend/API surface or remove it. Note it currently sends *decimal* strings
while `H9Backend.set_value` sends *hex* — one of them is likely wrong; the
working `set_value` path is authoritative.

### 6.5 `gpiozero` dependency has no platform marker
`pyproject.toml` line: `gpiozero>=2.0.1` installs everywhere. Since the app
gracefully degrades without it (`gpio_input.py` handles the import failure),
add the same `sys_platform == 'linux'` marker already used for `lgpio`
(NB: only if GPIO is unused on Windows dev machines — it is, per the existing
graceful fallback).

---

## Segment 7 — Repo housekeeping & entry points

**Files:** `main.py`, `test.py`, `ui_main.py`, `README.md`/docs cross-references. *Do not* touch `qt_worker.py` (Segment 1 owns it) or `scripts/`.

### 7.1 `test.py` duplicates `main.py`
Root `test.py` and `main.py` are near-identical "connect → dump preset → listen"
CLI utilities (different flags; `test.py` adds `--print-bpm`, `main.py` adds
`--next-preset`). Options: fold both into one CLI in `main.py` and delete
`test.py`, or keep both but move them together. Either way the root-level name
`test.py` is confusing next to `tests/` — pytest ignores it via
`testpaths=["tests"]` today, but a future config change could silently start
collecting it.

### 7.2 `thread.wait(2000)` blocks the UI thread on exit
`ui_main.py` ~line 234: `app.aboutToQuit.connect(lambda: thread.wait(2000))`
can freeze the window up to 2 s during shutdown (worse if combined with the
Segment 1 blocking sleeps). Reuse the worker's queued-shutdown pattern or
shorten the join.

### 7.3 Explanatory note (no code): entry-point naming
`[project.scripts]` maps `h9-control = "ui_main:main"` / `h9-control-cli =
"main:main"` to root modules. If Segment 7.1 consolidation happens, update both
lines. Document the CLI entry point in `readme.md` (currently only documented:
`ui_main.py` run commands).

### 7.4 Stale problem-doc (done by this rewrite)
The previous `PROBLEM.md` described bugs that were already fixed
(duplicate `TREMLO` key, `getattr(knob, "value")`, duplicate
`BUFFER_DURATION`, missing test suite, missing dev deps…). This file replaces
it. When a segment is completed, delete its entries here or move them to a
"Resolved" section in the same commit.

---

## Not tracked (per project owner)

- GPIO pin-number conflicts in `config.json` (e.g. two actions on pin 23) —
  the committed `config.json` is an example; add real assignments locally.
