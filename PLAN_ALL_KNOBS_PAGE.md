# Plan: full-knob page ("ALL KNOBS")

Status: implemented — branch `feature/all-knobs-page`.

Implementation notes:
- Ten knobs do not fit as touch targets on 1280x720, so the page uses a
  **scrollable 2-column grid** (5 rows) with large tiles instead of a fixed
  5x2 grid. Scrolling was accepted as the trade-off for finger-sized sliders.
- `--screenshot-page {dashboard,knobs,settings}` was added to `ui_main.py` so
  the new page can be captured with the existing screenshot workflow.

## Goal

Every H9 algorithm exposes 10 knob values, but the dashboard only shows the
first 4 entries of `config.knob_order`. Add a second page that shows **all 10
knobs** of the current algorithm, each directly adjustable by touch, reachable
from a dashboard button that is also bindable through `config.json`
(keyboard, GPIO and rotary encoders).

## What already works (reuse, don't rebuild)

- `PresetSnapshot.knobs_by_name` already contains **all 10** parsed knob values
  keyed by the algorithm's knob names (`h9control/domain/preset.py`).
- `H9FullAlgorithmData.knob_names(algo_key)` gives the canonical order.
- `H9DeviceWorker._adjust_single_knob()` already resolves the H9 MIDI CC for
  **any** knob name by reversing the algorithm's knob list and using
  `CC = 21 + 1-based-index` (CC 22–31), verified empirically for DLY-A/DLY-B.
- `_knob_overrides` is already a per-knob dict keyed by knob name, cleared on
  preset change, and `format_knob_value()` already renders pretty labels
  (note divisions, feedback %, filter, speed, DLYMIX).
- `MainWindow` already uses a `QStackedWidget` with dashboard ↔ settings, so a
  third page is a small addition.
- Action names in `ShortcutsConfig` are already resolved in two places:
  `DashboardWidget._install_shortcuts()` (keyboard) and
  `H9DeviceWorker._setup_gpio_bindings()` (GPIO / rotary encoders).

## Gap

1. `DashboardState` only carries the 4 dashboard knobs; the page has no full
   snapshot to render (although the worker has it internally).
2. `adjust_knob()` validates the knob name against `_last_state.knobs`, i.e.
   only the 4 visible ones, so a page knob could not be adjusted.
3. `_state_with_overrides()` only rebuilds the 4 dashboard knobs, so a page
   reading overrides would not update.
4. There is no absolute "set value" path — only relative `+/-5%` steps. A
   touchscreen page wants a slider (drag to value), not 20 taps.
5. No page widget, no navigation button, no `show_all_knobs` action name.

## Design

### 1. State: one snapshot, two views

Extend `DashboardState` (`h9control/app/state.py`):

```python
knobs: tuple[KnobBarState, ...] = ()       # dashboard slots (unchanged)
all_knobs: tuple[KnobBarState, ...] = ()   # every knob, algorithm order
```

`KnobBarState` stays as-is; add a `cc_value: int | None` field (0–127) or
derive it in the page from `raw_value`. Recommendation: keep `KnobBarState`
unchanged and let the page convert `raw_value → CC` with a shared helper.

`_preset_state()` / `_build_knobs()` in `qt_worker.py` build both from the
single `PresetSnapshot`:

- `knobs` → unchanged (`config.knob_order`, first 4 shown by the dashboard).
- `all_knobs` → `H9FullAlgorithmData.knob_names(preset.algorithm_key)` order,
  values from `preset.knobs_by_name`, overrides applied, `pretty` already
  formatted. Unknown algorithm → empty tuple (page shows a "no data" state).

`_state_with_overrides()` and `_state_from_prev()` must carry `all_knobs`
through so lock toggles / BPM updates / reconnects do not blank the page.

### 2. Domain helpers (testable, no Qt, no MIDI)

New `h9control/domain/knob_control.py`:

- `knob_cc_number(algorithm_key, knob_name) -> int | None`
  (extracted from the inline logic in `_adjust_single_knob`, so the mapping is
  covered by a unit test instead of only hardware tests).
- `knob_cc_from_raw(raw) -> int` / `knob_raw_from_cc(cc) -> int`
  (single source for the 14-bit ↔ 7-bit conversions currently duplicated in
  `knob_display.py` and `qt_worker.py`).
- Optional: `snap_cc_for_timefactor_delay(cc) -> int` using the existing
  `_TIMEFACTOR_DELAY_NOTE_POINTS`, so dragging DLY-A/B snaps to real divisions
  instead of showing "1/4" for every other CC step.

### 3. Worker: adjustments for any knob

In `H9DeviceWorker`:

- `adjust_knob(name, delta)`: validate against `all_knobs` (fall back to
  `knobs` when empty) instead of only the 4 dashboard slots. Lock pairing
  (`DLY-A/B`, `FBK-A/B`, `PICH-A/B`) is unchanged.
- New slot `set_knob_value(knob_name: str, cc_value: int)`:
  - clamp to 0–127,
  - apply the same lock pairing,
  - route through a shared `_send_knob_cc(name, cc_value)` used by both the
    relative and absolute paths (removes the current duplication),
  - store the override on the CC grid exactly like `adjust_knob` does.
- `adjust_knob_slot` stays for the 4 hardware/keyboard slots.

No blocking work is added: sending a CC is a short transport write, same as
today. UI-side throttling (below) bounds message rate.

### 4. New page widget: `h9control/app/ui/qt_knobs.py`

`KnobPageWidget(QWidget)`:

- Signals: `back_requested`, `set_knob_requested(str, int)`,
  `adjust_knob_requested(str, int)`.
- Header: preset number/name, algorithm display name, `BACK` button
  (mirrors `SettingsWidget`).
- Body: a **scrollable** `QGridLayout` with **2 columns × 5 rows** for the 10
  knobs (1280×720 → ~600 px wide tiles, so sliders stay finger-sized; the
  page scrolls vertically like the settings page).
- Each tile (`_KnobTile`):
  - knob name label (`knob_label` font),
  - large value label using `pretty` (or `NN%`),
  - horizontal `QSlider` range 0–127 with a thick handle (same styling
    approach as the brightness slider in `qt_settings.py`),
  - `−` / `+` buttons for fine steps (reuse `adjust_knob_requested`),
  - "LOCKED" badge for the secondary knob of an enabled lock pair, matching
    dashboard semantics.
- Missing knob → tile disabled/blank, not hidden, so the grid never reflows.
- Update path: `apply_state(state)` reads `state.all_knobs`.
  **Do not move a slider the user is currently dragging**: track
  `_active_knob` set on `sliderPressed`, cleared on `sliderReleased`, and skip
  that tile during state updates (otherwise the 300 ms preset refresh fights
  the finger).
- Send policy: `valueChanged` starts/restarts a single-shot ~40 ms `QTimer`
  that emits the latest CC value; `sliderReleased` flushes immediately. This
  keeps a drag at ≤25 CC messages/s and guarantees a final value.
- Optional snap: for `DLY-A`/`DLY-B` on TimeFactor algorithms, snap the slider
  to the nearest division point on release.

### 5. Navigation and mappable button

- `DashboardWidget`: add signal `all_knobs_requested`, a header button
  `ALL KNOBS` (`QPushButton`, same style/height as `SETTINGS`), wired to the
  signal; add `"show_all_knobs"` to `_install_shortcuts()`'s `action_map`.
- `MainWindow`: construct the page, add it to the stack, wire
  dashboard/settings → page and page `back_requested` → dashboard; expose
  `_show_knob_page()`.
- `ui_main.py`: connect `window.dashboard.all_knobs_requested` and
  `window.dashboard.set_knob_requested` / `adjust_knob_requested` to the
  worker with `QueuedConnection`, and `worker.state_changed` to the new page.
- GPIO/rotary: add `"show_all_knobs"` to `_setup_gpio_bindings()`'s
  `action_map`, emitting a new `all_knobs_requested` signal on the worker;
  `MainWindow` connects that signal to `_show_knob_page()`. The worker never
  touches widgets directly.
- Page shows only *current* state; entering the page does not need a new
  program dump (the last emitted state already has `all_knobs`). Optionally
  emit `connect_refresh_requested` when the page is shown and the state is
  stale, but no new request type is required.

### 6. Config

- `ShortcutsConfig.default()` adds `"show_all_knobs": ["K"]` (K is free; the
  number row and QWERTY row are already used).
- Accepted in existing config sections, no schema change:
  - `shortcuts.keyboard.show_all_knobs: ["K"]`
  - `shortcuts.gpio.show_all_knobs: { "pin": …, … }`
  - rotary encoders: `action_cw` / `action_ccw` / modifiers may reference it.
- Note: `ConfigManager.load()` currently replaces the whole keyboard map when
  the file has one, so an existing `config.json` will not gain the `K` binding
  automatically. The plan is to keep that behavior (no silent rebinding) and
  document the new action name in `README.md` / this file. A one-time merge of
  missing default actions is a separate decision.
- No settings-dialog control: shortcut editing is config-file based today for
  every action; adding a keyboard editor is out of scope.

### 7. Tests

- `tests/test_knob_control.py`: `knob_cc_number` for DIGDLY (DLY-A → CC 24,
  MIX → CC 22), unknown knob → `None`; raw/CC round-trip and clamping.
- `tests/test_knob_page.py` (offscreen, pattern from `test_dashboard.py`):
  renders 10 tiles from `all_knobs`; `+`/`−` emit deltas with the right name;
  slider movement emits an absolute CC in 0–127; state updates do not move the
  slider being dragged; locked secondary tile shows the badge; `BACK` emits.
- `tests/test_worker_logic.py`: `_preset_state` exposes all 10 knobs in
  algorithm order while `knobs` stays at the configured 4; `adjust_knob` works
  for a knob outside the first 4; `set_knob_value` clamps and sends the
  expected CC (fake transport); preset change clears overrides for page knobs.
- `tests/test_dashboard.py`: `ALL KNOBS` button and `show_all_knobs` shortcut
  emit `all_knobs_requested`.
- `tests/test_config.py`: default shortcut contains `show_all_knobs`.

### 8. Layout constraints / risks

- Target screen is fixed 1280×720. The page intentionally scrolls (2-column
  grid); verify by screenshot with
  `uv run python ui_main.py --simulate-h9 --no-audio --no-gpio --no-config-save
  --screenshot <path> --screenshot-page knobs` and by running the app on the
  Pi. Only the visible 3.5 rows are readable without a swipe, which is the
  accepted trade-off for larger touch targets.
- 7-bit CC quantization is coarse for some parameters (pitch, filter). The
  pretty formatter re-quantizes from the stored 14-bit value, so labels stay
  truthful; optional division snapping improves DLY-A/B. If finer control is
  ever needed, SysEx knob writes (`0x200` keys) would be a separate project —
  current firmware path is CC only.
- Lock behavior: the page must grey out/mark the secondary knob exactly like
  the dashboard, otherwise touching it would appear to work while being
  overridden by the lock.
- `_knob_overrides` currently keys by knob name string; keep names uppercase
  consistently when reading/writing so page and dashboard do not diverge.

## Suggested commit sequence

1. `domain: extract knob CC mapping + raw/CC helpers` (with unit tests).
2. `state/worker: expose all preset knobs and support absolute knob set`
   (with worker tests).
3. `ui: add full-knob page widget` (with page tests).
4. `ui: dashboard button, stack navigation and GPIO/shortcut action`
   (with dashboard/config tests).
5. `docs: document show_all_knobs action and page`.

## Out of scope

- Editing `knob_order` from the settings dialog (still a TODO there).
- Reading knob names for algorithms the H9 dump names differently than
  `ALGO_MAP` (falls back to "no knob data" state).
- Persisting per-knob values across presets (the H9 owns preset state).
