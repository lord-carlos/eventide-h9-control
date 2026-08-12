---
name: h9-screenshots
description: Use when reviewing H9 Dashboard visual changes, taking screenshots, or comparing simulated H9 UI states with an AI agent.
---

# H9 Dashboard Screenshots

Use the built-in simulated H9 and screenshot mode for deterministic visual
checks. Do not require a physical H9 for visual testing.

## Capture A Screenshot

Run this command from the repository root:

```text
uv run python ui_main.py --simulate-h9 --simulate-preset 0 \
    --screenshot artifacts/dashboard.png --exit-after-screenshot \
    --screenshot-width 1280 --screenshot-height 720 \
    --no-audio --no-gpio --log-level INFO
```

The command launches the real Qt dashboard, waits for the initial state, saves
the dashboard content, and exits. Use `1280x720` for the production display.
The screenshot does not include the native operating-system title bar.

Use `--screenshot-delay-ms` when a visual change needs more time to settle.
Use `--screenshot-width` and `--screenshot-height` when a different viewport is
required. Keep dimensions fixed when comparing screenshots.

## Capture Different States

The simulator provides four deterministic starting presets:

```text
--simulate-preset 0  PRISTINE DIGITAL / DIGDLY
--simulate-preset 1  WARM ECHO / VNTAGE
--simulate-preset 2  DARK TAPE / TAPE
--simulate-preset 3  MOTION DELAY / MODDLY
```

Capture each state to a separate file when reviewing layout or typography:

```text
uv run python ui_main.py --simulate-h9 --simulate-preset 1 \
    --screenshot artifacts/warm-echo.png --exit-after-screenshot
```

The fake device also supports preset navigation, BPM reads and writes, and
knob Control Changes. Use those interactions in an interactive run when the
change affects state updates rather than initial rendering.

## Inspect And Compare

After capture, inspect the PNG with the image-reading tool. Review these areas:

- Preset and algorithm labels
- Knob visibility, labels, progress bars, and raw values
- BPM controls and status indicator
- Spacing at the fixed viewport size
- No dashboard scrolling or clipping
- Behavior when presets have different names and values

Use image-diff tooling for automated regression detection. Use AI visual
inspection for qualitative issues such as alignment, hierarchy, clipping, and
poor contrast. Do not treat an AI visual judgment as a replacement for an
exact or perceptual diff when pixel stability matters.

Generated files belong under `artifacts/`, which is ignored by Git.
