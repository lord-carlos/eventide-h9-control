# Plan: Reliability and Resilience Improvements (v2)

## Implementation status

*Closed out 2026-10-29: §1–§4 are implemented, §5 was left unchanged by owner
decision. The design record below is kept as written — its TODO markers describe
the plan at the time, not open work.*

| Section | State |
|---|---|
| §1 MIDI connection lifecycle | **Done** — supervisor state machine, RX fault isolation, connection generations, truthful states, resync on reconnect, exception-safe connect/close, port presence |
| §2 Audio (USB hotplug) | **Done** — minimal patch, `beat_detector.py` only; late/missing devices now retry indefinitely |
| §3 Requests, timers, shutdown | **Done, folded into §1** — retry-once idempotent reads, waiter cancellation, generation guards, timer cleanup, shutdown ordering |
| §4 Config safety | **Done** — atomic `tmp → fsync → os.replace`, stale `.tmp` cleanup, save failures surfaced in Settings |
| §5 Pi service / crash recovery | **No change** — owner decision. `linux/**` and `INSTALL_RASPBERRY_PI.md` untouched, and systemd still supervises labwc rather than Python, so a crashed app still leaves the last frame on screen |

Branch **`feature/resilience-improvements`**, not merged to master. Merges:
`330fc82` (MIDI handle safety), `1b2e698` (config), `fa1410c` (worker
supervisor), `fc298bd` (audio). Feature commits: `bd3757c`, `26b0c42`,
`2127a94`, `1fb18ba`, `2959610`, `55e374a`. Suite: **244 passing** (183 before
this work).

Two fixes came out of review rather than the plan as written:

1. **Worker — stale RX threads must not report faults.** A thread from a
   superseded connection can still raise while it is being torn down; its report
   would cancel the *new* connection's waiters or tear it down. `_report_rx_failure()`
   now drops reports whose `connection_id` is not current (or whose stop event is
   set). (`1fb18ba`)
2. **Audio — clear the live BPM before the backoff, reset the flag on start.**
   `-1.0` is emitted before the recovery wait so the dashboard and continuous
   auto-sync stop using a tempo that is already gone, and `start()` resets
   `_needs_recovery` so a stale flag cannot trigger a spurious recovery
   immediately after a successful start. (`55e374a`)
3. **Worker — retry the tempo read once and never republish a stale
   `connected=True`.** The §3 requirement to retry idempotent reads covers the
   BPM read as well as the program dump, and a send failure during that read
   previously let `_refresh_state_once()` publish a connected state after the
   loss path had already torn the transport down. Both are covered in
   `tests/test_connection_supervisor.py`.

---

*Revised after a line-by-line review of the modules named in v1, then narrowed
by two owner decisions: the Pi service setup stays as it is, and the audio
pipeline may only be touched by a minimal, isolated patch. v1 contained several
items that were already implemented and several that cannot work as written
against the current thread model. This version keeps the goal (survive USB
unplug/replug, transient failures, unsafe shutdown) and replaces the rest with
concrete, buildable steps.*

Status key: **TODO** · **DONE already** (do not redo) · **DROPPED** (reason given)

---

## Ground rules discovered during review

These constrain every section below; they are the reason parts of v1 were
rewritten.

1. **The worker thread is not always free.** `_refresh_state()` and
   `_connect()` run synchronously on the worker thread and block in
   `_wait_for_frame()` for up to 2 s. A Qt signal queued to the worker thread
   cannot preempt a blocked wait. Therefore: **waiter cancellation must be
   performed by the RX thread directly** (under `_waiters_lock`); only the
   teardown + retry scheduling goes through the queued slot.
2. **Simulate mode and the test suite have no Qt event loop.** Tests call
   `connect_or_refresh()` directly with no `app.exec()`, so `QTimer` never
   fires. The supervisor must keep simulate mode (and any explicit
   connect call) fully synchronous; timers only drive *retries*.
3. **`_connect()` is part of the `BpmSyncMixin` contract** (`bpm_sync.py:64,70,95`).
   After it returns, either `_transport` is usable or `None`. It must not
   become "start an attempt and return later".
4. **`_emit_state()` has side effects** — it runs `_check_auto_bpm_sync()`,
   which can send MIDI. Disconnect/teardown states must be emitted after
   `_transport` is cleared, or with a generation guard.
5. **Transitions must be idempotent.** The RX thread may still hold a local
   reference to a transport that the worker thread has just closed. Any
   handler can legitimately run twice or against a dead handle; `try/except`
   plus a state check is the contract, not a lock.
6. **`ConfigManager` write batching is already correct** (dirty flag +
   debounced timer + `atexit`). Only the write itself needs to become atomic.

---

## 1) MIDI connection lifecycle

### Already done (do not redo)

- `H9Midi.connect()` already calls `close()` first (`h9_midi.py:65`), so
  re-connecting no longer leaks the *previous* handles.
- `H9Midi` already exposes public `send_program_change` /
  `send_control_change` (`h9_midi.py:115,131`).
- `MidiTransport` no longer reaches into `H9Midi` privates.

### Remaining real problems

| Problem | Location |
|---|---|
| `receive_pending()` is not wrapped; a USB unplug kills the RX thread silently while `connected` stays `True` | `qt_worker.py:854` |
| Reconnect only when `_transport is None`; a dead non-`None` transport is never replaced | `qt_worker.py:354,373,510,721` |
| `_connect()` reports `connected=True` before any successful sync | `qt_worker.py:613-621` |
| Refresh/command failures keep `prev.connected` | `qt_worker.py:704` |
| No retry at startup or when the device reappears | `ui_main.py:248` |
| `H9Midi.close()` is not exception-safe (an `_in.close()` failure skips `_out.close()` and both are left non-`None`) | `h9_midi.py:86-92` |
| A failure opening the input after the output leaks the output handle | `h9_midi.py:76-82` |

### State machine (TODO)

States: `disconnected → connecting → connected → reconnecting`.

New worker state:
`_connection_state`, `_connection_id` (monotonic, bumped per attempt),
`_retry_attempt`, `_retry_delays = (1, 2, 5, 10, 30)`, single-shot
`_supervisor_timer`, `_shutting_down`.

**`_attempt_connect() -> bool`** (synchronous; this is `_connect()` plus the
checking v1 was missing):

1. Return `False` if `_shutting_down`.
2. Stop `_supervisor_timer`; tear down any existing connection (idempotent).
3. `_connection_id += 1`; capture `gen`.
4. Emit `connected=False, "Connecting…"`.
5. Open the transport through a single seam method
   `_open_transport() -> (midi | None, transport)` — a separate method so
   tests can monkeypatch one thing instead of the `H9Midi` constructor.
6. Assign `_transport`, start RX.
7. Drain stale input, then run the initial sync (program dump with one retry,
   then best-effort BPM). **Only on sync success** set
   `_connection_state = connected` and emit `connected=True`.
8. On any failure: tear down, reset resync bookkeeping, schedule a retry, emit
   the truthful state, return `False`.

**Retry scheduling** — `_schedule_retry()` starts `_supervisor_timer` with
`_retry_delays[min(_retry_attempt, 4)]` and increments `_retry_attempt`.
Successful connect resets it to 0. In simulate mode this is a no-op (the
simulated transport cannot fail). F5 / `connect_or_refresh()` resets the
backoff and attempts immediately.

**Loss detection** — three sources, one entry point:

- RX exception → RX thread calls `_cancel_waiters(RuntimeError("Disconnected"))`
  *directly*, then emits `_connection_lost.emit(reason)` (cross-thread → queued).
- Send exception → worker calls `_on_connection_lost(reason)` directly (it is
  already on the worker thread) and returns immediately from the caller.
- Port disappearance → RX loop checks `transport.is_port_present()` (new,
  backed by `mido.get_output_names()`) at most every 3 s while idle.

`_on_connection_lost(reason)`: no-op if `_shutting_down` or not currently
connected (idempotency). Otherwise set `reconnecting`, clear the transport,
stop `_event_refresh_timer`, cancel waiters, emit
`connected=False, "Disconnected: …"` (keeping the last preset snapshot so the
UI can grey it out), and schedule a retry.

**`connect_or_refresh()`** becomes: reset backoff; if not connected, attempt
once; if still not connected, return; otherwise `_refresh_state()`.
This preserves the existing single-entry behaviour used by F5, GPIO and
startup, and removes the double-refresh that a naive "sync inside `_connect`"
would introduce.

### RX fault isolation + generations (TODO)

- Wrap `receive_pending()` in `try/except Exception`; log with
  `connection_id` and device prefix; go through the loss path above.
- Keep decoding frames one by one; a malformed SysEx frame is discarded, not
  fatal (`decode_eventide_sysex` already returns `None`).
- `_FrameWaiter` gains `connection_id` and `cancel(exc)`; `wait()` raises the
  cancellation error instead of returning `None`.
- `_wait_for_frame` captures the current generation in the waiter.
  `_try_deliver_to_waiters` only matches waiters whose generation is current;
  stale waiters are cancelled and removed.
- `_schedule_state_refresh` captures the generation and goes through
  `_refresh_if_current(gen)`, so a pending 300 ms refresh after a preset
  change cannot run against a new connection.
- Resync on reconnect: re-read the program from the device (never trust
  `_current_program`), clear `_knob_overrides`, set
  `_last_sent_auto_bpm = None`, reset the preset-change detector window and
  `_last_event_refresh_at`, and do **not** replay any queued outage actions.

### Truthful states (TODO)

| Situation | Result |
|---|---|
| Initial program dump fails twice | stay `disconnected`, schedule retry, status `"Connect failed: …"` |
| BPM read fails while connected | `connected=True`; keep `_last_good_bpm`; status note `"Connected (BPM unavailable)"` when there is no last good value |
| Program dump timeout, first failure | `connected=True`, last snapshot kept, status `"H9 not responding (retrying)"` |
| Program dump timeout, second consecutive failure | go through `_on_connection_lost()` |
| Send I/O error | go through `_on_connection_lost()` immediately |

### Safe connect/close (TODO)

- `H9Midi.close()`: close both handles inside `try/finally`-style guards so
  one failure cannot skip the other, and always clear both fields.
- `H9Midi.connect()`: if opening the input fails, close the output that was
  already opened before re-raising.
- `H9Midi.is_port_present()`: returns whether a matching output port still
  exists. `MidiTransport.is_port_present()` delegates to it. Add the same
  method to `SimulatedH9Transport` (always `True`) so it satisfies the
  `H9Transport` protocol, and have the RX loop use
  `getattr(transport, "is_port_present", lambda: True)()` so third-party or
  test doubles without the method still work.
- `shutdown()` order: set `_shutting_down`, stop both timers, unbind GPIO,
  cancel waiters (`RuntimeError("Shutting down")`), tear down (stop RX, close,
  join ≤1 s), clear `_transport`/`_midi`/`_waiters`. No state emission during
  shutdown.

### Files

- `h9control/app/ui/qt_worker.py`
- `h9control/transport/h9_midi.py`
- `h9control/transport/midi_transport.py`
- `h9control/transport/simulated_h9.py` (`is_port_present()`)
- `tests/test_worker_logic.py` (one test updated, see §6)

---

## 2) Audio (USB hotplug) resilience

**Owner constraint: the detection pipeline works and took a long time to get
right. This section is a deliberately small patch — no changes to the signal
path, the ring buffer, the tempo maths, or the working open path.**

### Scope

Two behaviours only:

1. **Late device** — the app starts with no usable audio input and keeps
   retrying until a device appears.
2. **Device loss** — the stream dies or stops delivering callbacks, and keeps
   retrying until it comes back, while telling the worker the live BPM is gone.

### Do not touch (working, proven)

- `_audio_callback`, `_read_ring_buffer`, the SPSC margin logic.
- `_calculate_bpm`, `_correct_harmonics`, `_update_stabilized_bpm`,
  `_refine_beats`, `_calculate_bpm_from_ibis`.
- The forced `SAMPLE_RATE` policy and `_recalculate_buffer_sizes`.
- `_extract_stereo_channels_fast` and its short/empty-list guards.
- Channel clamping and the `_find_fallback_device` behaviour used by the
  **initial** open.
- Xrun handling: `input_overflow → _needs_recovery = True` stays exactly as it
  is. (v1 proposed aggregating xruns; dropped — it is part of what works.)
- `h9control/audio/device_refresh.py` — unchanged.

### Changes, in diff-sized pieces

1. **`start()` — keep monitoring when the first open fails.**
   The success path is behaviourally identical (`_start_stream()` first, then
   start the analysis thread). Only the failure branch changes:
   `running = False; return` → set `_needs_recovery = True` and still start the
   analysis thread. The loop then retries via the recovery path.
2. **`_analysis_loop` — remove the two permanent-death exits.**
   Delete the `if self.stream_dead: break` block and the
   `callback_stall_count >= 3 → stream_dead = True; break` escalation. Keep the
   existing "stall >2 s sets `_needs_recovery`" detection untouched. Remove the
   now-unused `stream_dead` attribute.
3. **`_should_attempt_recovery()` — delete it.**
   The max-3-attempts check is the reason recovery gives up, and the 30 s
   cooldown only makes reconnection slow. Pacing is already provided by the
   backoff inside `_attempt_recovery()`.
4. **`_attempt_recovery()` — never give up, and stay interruptible.**
   - Promote `RECOVERY_BACKOFF_DELAYS = (1, 2, 4, 8, 16, 30)` to a module
     constant so tests can assert it; index by attempt with the last value
     repeating.
   - Replace both `time.sleep(...)` calls with `self._stop_event.wait(...)`;
     return `False` immediately when the stop event was set.
   - On success: keep the existing resets (`recovery_attempts`,
     `last_recovery_time`, `total_recoveries`, `_needs_recovery`,
     `callback_stall_count`) and reset the BPM-cleared flag.
   - On failure: return `False`; the loop retries at the next backoff step.
5. **Device identity, used only during recovery.**
   - After a successful open, record `(dev_info["name"], dev_info["hostapi"])`
     in `_open_stream()`.
   - Add `device_index: int | None = None` to `_start_stream()`. With the
     default `None` it behaves exactly as today (config id → default → existing
     fallback); recovery passes a resolved index.
   - `_resolve_saved_identity()` enumerates `sd.query_devices()` and returns the
     index whose `(name, hostapi)` matches; `None` on no match or on error, which
     falls back to today's path.
   - This fixes replug index shifts without touching `config.json` or the
     settings dialog.
6. **Clear live BPM once on loss.**
   In `_attempt_recovery()`, before closing the stream, if `self.bpm > 0` and
   the flag is not set, `self.bpm_detected.emit(-1.0)` and set the flag. The
   worker already maps `-1.0 → None` via `sanitize_live_bpm`, so the dashboard
   clears and continuous auto-sync stops sending. Do **not** reset `self.bpm`:
   the stabilizer keeps its last value and confirms a new tempo normally after
   recovery.
7. **Status attribute (no behaviour, no UI).**
   `self.status_text` ∈ `"Starting"`, `"Listening"`,
   `"Audio disconnected (retrying)"`, `"Stopped"`; log transitions at INFO.
   `DashboardState` is not extended (no room in the fixed 1280×720 layout).

### Risk controls

- Land this as its own commit on its own branch so it can be reverted without
  touching the MIDI/config work.
- Add a happy-path regression test (open → analyze → stop) **before** changing
  anything, so "still works" is measurable rather than judged by ear.
- Keep `stop()`'s join at 2 s; every new wait is on `_stop_event`, so it stays
  prompt.
- Field-verify in this order: normal start → settings restart → unplug while
  live → replug → start with no device attached.
- If the field test regresses, revert the commit; MIDI/config work is
  independent.

### Files

- `h9control/audio/beat_detector.py` only.

---

## 3) Requests, timers, shutdown hardening

- **Retry idempotent reads once**: `SYSEXC_TJ_PROGRAM_WANT` and the BPM
  `VALUE_WANT` may be re-sent once on timeout before declaring failure.
  Relative commands (next/prev preset, knob deltas) and `VALUE_PUT` are never
  retried. Implement inside the request helpers, not at the call sites.
- **Cancel waiters on disconnect**: `_cancel_waiters(exc)` swaps the waiter
  list out under `_waiters_lock` and calls `waiter.cancel(exc)` on each. Safe
  to call from either thread, safe to call twice.
- **Generation checks**: see §1. Every waiter, scheduled refresh and retry
  timer carries the generation it was created for; stale ones are ignored.
- **Per-request timeouts**: keep the current 2 s (program) / 1 s (BPM).
  Do **not** add random jitter — it buys nothing for a single local device and
  makes tests nondeterministic. Log timeouts with command name, generation and
  elapsed time.
- **Timer cleanup**: `_event_refresh_timer`, `_supervisor_timer` and any
  generation-guarded `singleShot` are stopped/ignored on disconnect and in
  `shutdown()`.
- **Thread safety**: no new locks around `_transport`. RX uses a local
  reference and all handlers are idempotent (ground rule 5). `_waiters_lock`
  and `_stream_lock` remain the only locks.

---

## 4) Configuration safety (atomic writes)

**DONE already:** debounced dirty-flag saves, no-change write skip,
`atexit` flush, corrupt-file preservation as `config.json.bak`, `load_error`
surfaced in Settings.

**TODO:**

1. `_write_file()` → write `config.json.tmp` in the same directory, `flush()`,
   `os.fsync(fileno())`, `os.replace(tmp, path)`. Best-effort directory fsync
   on POSIX only (Windows cannot open a directory); failures there are logged
   at DEBUG and ignored.
2. Clean up a stale `config.json.tmp` on load (a crash before `os.replace`
   leaves it behind).
3. Add `save_error: str | None` to `ConfigManager`, set on a failed write and
   cleared on success. Surface it in the Settings status banner next to the
   existing `load_error` banner (`qt_settings.py:658-674`, reuse
   `_apply_status`).
4. **No `.bak` on every successful save.** It doubles SD-card writes and
   defeats the debounce; with an atomic replace a crash cannot produce a
   partially written file, so the load-time backup is sufficient.

### Files

- `h9control/app/config.py`
- `h9control/app/ui/qt_settings.py` (save-error banner, ~10 lines)

---

## 5) Raspberry Pi service / crash recovery — no change

**Owner decision: the current `labwc -s start-app.sh` + systemd setup works
well enough. Do not modify `linux/h9-control.service`, `linux/start-app.sh`,
`linux/install.sh` or `INSTALL_RASPBERRY_PI.md` as part of this work.**

The known limitation is recorded here so it is not rediscovered as a bug:
systemd supervises labwc, not Python, so a Python crash leaves the last frame
on screen until the unit or the machine is restarted. Accepted as-is.

If it ever needs fixing, the smallest safe fix is a `trap` + respawn loop
inside `start-app.sh` (about 10 lines, no unit changes). A two-unit
compositor/app split was evaluated and rejected: it needs Wayland socket and
seat plumbing that is more likely to break a working install than the loop.

---

## 6) Testing strategy

Constraint: tests must not depend on a running Qt event loop or real sleeping.
Use fake clocks, call the transition methods directly, and assert on
`_retry_attempt` / timer intervals rather than waiting for timers.

### New tests

**`tests/test_h9_midi_resilience.py`**
- `close()` closes both handles even when the input close raises, and leaves
  both fields `None`.
- Input-open failure closes the already-opened output and re-raises.
- `connect()` twice does not leave the first pair open.
- `is_port_present()` follows `get_output_names()`.

**`tests/test_connection_supervisor.py`** (monkeypatch
`H9DeviceWorker._open_transport`)
- RX `receive_pending()` raising → waiters cancelled immediately, loss slot
  emits `connected=False`, state is `reconnecting`.
- Loss while connected schedules exactly one retry; backoff sequence is
  1/2/5/10/30 and caps at 30.
- Successful reconnect resets `_retry_attempt`, clears `_knob_overrides`,
  resets `_last_sent_auto_bpm`, and re-reads the program from the device.
- A frame arriving with an old `connection_id` is not delivered.
- Two consecutive program-dump timeouts trigger the loss path; one does not.
- `shutdown()` while a retry is pending stops the timer and never reopens.
- A BPM-only failure keeps `connected=True` and the last good BPM.

**`tests/test_beat_detector_recovery.py`** (monkeypatch `beat_detector.sd`)
- Happy-path regression first: `start()` with a working fake stream opens once,
  starts exactly one analysis thread, and `stop()` closes and joins.
- `start()` keeps `running=True` and starts the analysis thread even when the
  first `_start_stream()` fails.
- Backoff waits are interruptible: setting the stop event mid-wait makes
  `stop()` return promptly (no 30 s hang).
- Recovery keeps retrying past 3 failures and caps the delay at 30 s.
- `-1.0` is emitted once on live→lost, not on every retry; the flag resets on
  successful recovery.
- `_resolve_saved_identity()` finds the device after an index shift, and
  returns `None` when it is gone (so the old fallback path still applies).
- Xrun handling is unchanged: a single `input_overflow` still sets
  `_needs_recovery` (guards against accidental "improvement").

**`tests/test_config.py` (additions)**
- A successful save leaves no `.tmp` behind.
- `os.replace` is used (patch it and assert the call).
- A write failure leaves the previous file byte-identical and sets
  `save_error`; the next successful save clears it.
- A stale `.tmp` is removed on load.

### Existing tests that must change (intentionally)

- `test_worker_logic.py::test_refresh_after_shutdown_emits_error_state` asserts
  `connected is True` after a failed refresh. That encodes the misleading
  state this plan removes. New expectation: no exception, `connected=False`,
  status `"Disconnected"`.
- `_FrameWaiter` tests stay valid; the new `connection_id` argument defaults
  to 0 and `wait()` keeps returning `None` on timeout.

### Manual / field tests

- Boot with no H9 attached → retry loop runs, connects when plugged in.
- Unplug while connected → status shows disconnected, reconnects and resyncs
  the *device's* current program (change the preset on the pedal while
  unplugged to prove it is not cached).
- Unplug/replug three times in ten seconds → no thread leak, no stuck
  "Connecting…".
- Audio device unplug/replug with continuous BPM sync active → live BPM
  clears, then resumes; auto-sync does not send a clamped 20 BPM.
- Audio: start the app with the USB interface unplugged, then plug it in →
  detection starts without a settings refresh.
- Power-cycle during a settings change → `config.json` still parses.

---

## Implementation order

1. **`H9Midi` safety + port presence** — small, no app behaviour change yet.
2. **Worker supervisor, RX isolation, waiter cancellation, generations,
   truthful states, resync, shutdown ordering** — the bulk of the work.
3. **Audio minimal recovery patch** (own branch/commit; see §2 risk controls).
4. **Atomic config writes + `save_error` surfacing.**
5. **Docs sweep**: `PROBLEM.md` follow-ups and any README text that now
   describes behaviour differently.

One commit per step. Steps 1–2 and 3–4 are independent and can be done in
parallel, but the audio patch must not be mixed with anything else.

---

## Removed from v1, with reasons

| v1 item | Why removed |
|---|---|
| "`H9Midi.connect()` leaks handles on re-connect" | Already fixed: `connect()` closes first (`h9_midi.py:65`). |
| "Keep existing serialized equality check / debounce / atexit" as work | Already implemented; listed as DONE instead. |
| "No-change skip" and "Autosave behaviour" subsections | Already done; no action. |
| "Guard access to `_transport`/`_midi` with synchronization" | A lock cannot make a closed rtmidi handle safe. Replaced with local-reference + idempotent-transition rules. |
| "Queued signal to the worker thread" for RX fault isolation | Insufficient on its own: the worker may be blocked in `_wait_for_frame`. Cancellation must happen on the RX thread. |
| "Timeout tuning … and jitter" | Jitter is pointless for one local device and hurts test determinism. |
| "`config.json.bak` last-known-good on successful writes" | Doubles SD-card writes; unnecessary once writes are atomic. |
| "Persist preferred device identity in config" | Not needed: settings already re-resolves by `(name, hostapi)`; an in-memory identity covers hotplug during a session and is only consulted on recovery. |
| "Expose audio status in the UI" | No room in the fixed 1280×720 layout and tooltips are out of scope. Expose `status_text` + logs, defer rendering. |
| "Validate `selected_channels` on reopen" | Already implemented (`beat_detector.py:304-319`). |
| "Stream open failures: schedule retry" as a v1 bullet | Kept, but reduced: no new supervisor thread, no new open path — one branch in `start()` plus the existing recovery loop. |
| "Aggregate xruns before recovering" (v2 draft) | Dropped for the same reason: `input_overflow → _needs_recovery` is part of the proven behaviour. Leave it alone. |
| "Restructure `_analysis_loop` so the loop owns the initial open" (v2 draft) | Dropped: `start()` keeps its existing open-first flow; only the failure branch changes. |
| All Raspberry Pi service work (respawn loop, `Restart` policy, logging env, `TimeoutStopSec`, rotation guard, install/doc updates) | Owner decision: the current `labwc` + `start-app.sh` setup works well enough. Recorded as a known limitation instead of a change. |

---

## Notes

- Simulate mode must stay fully synchronous and must never need an event loop.
- Do not replay queued actions after an outage (especially relative
  next/prev/knob-delta commands).
- Every reconnect logs with `connection_id` and device prefix so field logs can
  be correlated.
- Follow existing patterns: dataclasses for state, `QtCore.Signal`/`Slot`,
  mixins for worker behaviour, small functions.
