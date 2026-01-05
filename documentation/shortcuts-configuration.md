# Keyboard and GPIO Shortcut Configuration

The H9 Control dashboard supports configurable keyboard shortcuts and Raspberry Pi GPIO hardware buttons loaded from `config.json`.

## Configuration Structure

Shortcuts are defined in the `shortcuts` section of `config.json`:

```json
{
  "audio": { ... },
  "shortcuts": {
    "keyboard": { ... },
    "gpio": { ... }
  }
}
```

## Keyboard Shortcuts

Keyboard shortcuts map action names to one or more key sequences:

```json
"keyboard": {
  "next_preset": ["Right", "N"],
  "sync_live_bpm": ["D", "Return"],
  "adjust_dly_a_up": ["1", "Up"]
}
```

### Features

- **Multiple keys per action**: Each action can have multiple key bindings (e.g., `["Right", "N"]`)
- **Multiple actions per key**: One key can trigger multiple actions if it appears in multiple action lists
- **Qt KeySequence support**: Use Qt key names like `"Space"`, `"Return"`, `"Escape"`, arrow keys (`"Up"`, `"Down"`, `"Left"`, `"Right"`), or modifiers (`"Ctrl+X"`, `"Shift+F1"`)

### Available Actions

| Action | Description |
|--------|-------------|
| `next_preset` | Switch to next preset (Program Change +1) |
| `prev_preset` | Switch to previous preset (Program Change -1) |
| `connect_refresh` | Connect to H9 or refresh current state |
| `settings` | Open settings screen |
| `sync_live_bpm` | Send detected live BPM to H9 |
| `adjust_bpm_up` | Increase BPM by 1 |
| `adjust_bpm_down` | Decrease BPM by 1 |
| `adjust_dly_a_up` | Increase Delay A knob |
| `adjust_dly_a_down` | Decrease Delay A knob |
| `adjust_dly_b_up` | Increase Delay B knob |
| `adjust_dly_b_down` | Decrease Delay B knob |
| `adjust_fbk_a_up` | Increase Feedback A knob |
| `adjust_fbk_a_down` | Decrease Feedback A knob |
| `adjust_fbk_b_up` | Increase Feedback B knob |
| `adjust_fbk_b_down` | Decrease Feedback B knob |

## GPIO Hardware Buttons (Raspberry Pi)

GPIO bindings map actions to physical buttons connected to Raspberry Pi GPIO pins:

```json
"gpio": {
  "next_preset": {
    "pin": 17,
    "pull": "up",
    "edge": "falling",
    "debounce_ms": 50,
    "hold_threshold_ms": 500
  }
}
```

### Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `pin` | int | BCM pin number (e.g., 17, 27, 22) |
| `pull` | string | Pull resistor: `"up"`, `"down"`, or `null` |
| `edge` | string | Edge detection: `"rising"`, `"falling"`, or `"both"` |
| `debounce_ms` | int | Debounce time in milliseconds (default: 50) |
| `hold_threshold_ms` | int | Time in ms to distinguish tap from hold (default: 500) |

### Tap vs Hold Actions

Each GPIO pin can trigger **two different actions** based on press duration:

- **Tap action** (`"action_name"`): Fires on button release if held < `hold_threshold_ms`
- **Hold action** (`"action_name_hold"`): Fires when button held ≥ `hold_threshold_ms`

Example: Pin 22 taps for BPM sync, holds for settings:

```json
"gpio": {
  "sync_live_bpm": {
    "pin": 22,
    "pull": "up",
    "edge": "falling",
    "debounce_ms": 50,
    "hold_threshold_ms": 500
  },
  "settings_hold": {
    "pin": 22,
    "pull": "up",
    "edge": "falling",
    "debounce_ms": 50,
    "hold_threshold_ms": 500
  }
}
```

**Note:** Use the base action name for tap (e.g., `"sync_live_bpm"`), and append `_hold` for the hold variant (e.g., `"settings_hold"` maps to the `settings` action).

### GPIO Library Dependency

GPIO support requires `gpiozero`:

```bash
uv add gpiozero
```

If `gpiozero` is not installed, GPIO bindings are silently ignored (useful for development on non-Pi systems).

## Example Configurations

### Minimal (Keyboard Only)

```json
{
  "shortcuts": {
    "keyboard": {
      "next_preset": ["Right"],
      "prev_preset": ["Left"],
      "sync_live_bpm": ["D"]
    },
    "gpio": {}
  }
}
```

### Full (Keyboard + GPIO)

See `config.example-with-gpio.json` for a complete example with multiple keys per action and GPIO tap/hold bindings.

## Wiring GPIO Buttons

For pull-up configuration (`"pull": "up"`):

```
GPIO Pin (e.g., 17) ----[Button]---- GND
```

For pull-down configuration (`"pull": "down"`):

```
3.3V ----[Button]---- GPIO Pin (e.g., 17)
```

Use `edge: "falling"` for pull-up, `edge: "rising"` for pull-down.
