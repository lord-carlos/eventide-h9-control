"""GPIO input handler for Raspberry Pi hardware buttons.

Supports tap/hold actions with configurable debouncing and hold thresholds.
Uses gpiozero for GPIO management with graceful fallback if not on a Pi.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

try:
    from gpiozero import Button, Device, RotaryEncoder  # type: ignore

    GPIOZERO_AVAILABLE = True

    # Explicitly load rpi-lgpio pin factory if available
    try:
        from gpiozero.pins.lgpio import LGPIOFactory  # type: ignore

        if Device.pin_factory is None:
            Device.pin_factory = LGPIOFactory()
            logging.getLogger(__name__).info(
                f"Loaded pin factory: {Device.pin_factory.__class__.__name__}"
            )
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Could not load rpi-lgpio pin factory: {e}. "
            f"GPIO may not work. Pin factory status: {Device.pin_factory}"
        )

except ImportError:
    GPIOZERO_AVAILABLE = False
    Button = None
    RotaryEncoder = None


@dataclass
class _GpioButtonState:
    """Tracks state for tap/hold detection on a single GPIO pin."""

    press_time: float | None = None
    hold_fired: bool = False


class GpioInputManager:
    """Manages GPIO button inputs with tap/hold action support.

    Each GPIO pin can trigger two different actions:
    - Tap action: Fires on button release if held < hold_threshold_ms
    - Hold action: Fires when button held >= hold_threshold_ms

    Thread-safe for use with Qt via callback functions that should be
    invoked on the main thread (e.g., using QMetaObject.invokeMethod).
    """

    def __init__(self) -> None:
        self._buttons: dict[int, Any] = {}  # Button instances when available
        self._button_states: dict[int, _GpioButtonState] = {}
        self._rotary_encoders: dict[str, Any] = {}  # RotaryEncoder instances by name
        self._modifier_states: dict[str, bool] = {}  # modifier_name -> is_active
        self._logger = logging.getLogger(__name__)

        if not GPIOZERO_AVAILABLE:
            self._logger.warning(
                "gpiozero not available - GPIO input disabled. "
                "Install with: uv add gpiozero"
            )

    def bind_action(
        self,
        *,
        pin: int,
        tap_action: Callable[[], None] | None = None,
        hold_action: Callable[[], None] | None = None,
        pull_up: bool = True,
        debounce_ms: int = 50,
        hold_threshold_ms: int = 500,
        modifier_name: str | None = None,
    ) -> None:
        """Bind tap and/or hold actions to a GPIO pin.

        Args:
            pin: BCM pin number (e.g., 17, 27, 22)
            tap_action: Callback for tap event (short press)
            hold_action: Callback for hold event (long press)
            pull_up: True for pull-up resistor, False for pull-down
            debounce_ms: Debounce time in milliseconds
            hold_threshold_ms: Time in ms to distinguish tap from hold
            modifier_name: If set, this button acts as a modifier for rotary encoders.
                          Modifier is active while the button is held down.
        """
        if not GPIOZERO_AVAILABLE:
            self._logger.debug(f"GPIO pin {pin} binding skipped (gpiozero unavailable)")
            return

        if tap_action is None and hold_action is None:
            self._logger.warning(f"GPIO pin {pin} has no tap or hold action")
            return

        try:
            # Create Button with debouncing
            button = Button(  # type: ignore
                pin,
                pull_up=pull_up,
                bounce_time=debounce_ms / 1000.0,
            )

            state = _GpioButtonState()
            self._button_states[pin] = state

            # When pressed: record time
            def on_press() -> None:
                state.press_time = time.time()
                state.hold_fired = False
                if modifier_name:
                    self._modifier_states[modifier_name] = True
                self._logger.debug(f"GPIO pin {pin} pressed")

            # When released: check if tap or already fired hold
            def on_release() -> None:
                if state.press_time is None:
                    return

                hold_duration_ms = (time.time() - state.press_time) * 1000
                self._logger.debug(
                    f"GPIO pin {pin} released after {hold_duration_ms:.0f}ms"
                )

                # Clear modifier state if applicable
                if modifier_name:
                    self._modifier_states[modifier_name] = False

                # If hold already fired, ignore release
                if state.hold_fired:
                    state.press_time = None
                    return

                # Check if it was a tap (short press)
                if hold_duration_ms < hold_threshold_ms:
                    if tap_action:
                        self._logger.info(f"GPIO pin {pin} tap action triggered")
                        try:
                            tap_action()
                            self._logger.debug(f"GPIO pin {pin} tap action completed")
                        except Exception as e:
                            self._logger.exception(
                                f"GPIO pin {pin} tap action failed: {e}"
                            )
                else:
                    # Long press but hold action wasn't triggered in held state
                    # This can happen if hold_action is None
                    if hold_action:
                        self._logger.info(
                            f"GPIO pin {pin} hold action triggered on release"
                        )
                        hold_action()

                state.press_time = None

            # When held: fire hold action if threshold exceeded
            def on_held() -> None:
                if state.hold_fired or state.press_time is None:
                    return

                if hold_action:
                    self._logger.info(f"GPIO pin {pin} hold action triggered")
                    hold_action()
                    state.hold_fired = True

            button.when_pressed = on_press
            button.when_released = on_release

            # Set up hold detection with threshold
            if hold_action:
                button.when_held = on_held
                button.hold_time = hold_threshold_ms / 1000.0

            self._buttons[pin] = button
            self._logger.info(
                f"GPIO pin {pin} bound (tap={tap_action is not None}, "
                f"hold={hold_action is not None}, threshold={hold_threshold_ms}ms)"
            )

        except Exception as e:
            self._logger.error(f"Failed to bind GPIO pin {pin}: {e}")

    def bind_rotary_encoder(
        self,
        *,
        encoder_name: str,
        clk_pin: int,
        dt_pin: int,
        action_cw: Callable[[], None] | None = None,
        action_ccw: Callable[[], None] | None = None,
        modifier_actions: dict[
            str, tuple[Callable[[], None] | None, Callable[[], None] | None]
        ]
        | None = None,
    ) -> None:
        """Bind clockwise and counter-clockwise actions to a rotary encoder.

        Uses quadrature encoding via gpiozero.RotaryEncoder to detect rotation direction.

        Args:
            encoder_name: Unique name for this encoder (for logging/tracking)
            clk_pin: BCM pin number for CLK signal (encoder A)
            dt_pin: BCM pin number for DT signal (encoder B)
            action_cw: Callback for clockwise rotation (default, used when no modifier active)
            action_ccw: Callback for counter-clockwise rotation (default, used when no modifier active)
            modifier_actions: Dictionary mapping modifier names to (cw_callback, ccw_callback) tuples.
                           When a modifier is active, its callbacks override the defaults.
                           Modifiers are checked in the order they appear in the dict (Python 3.7+ preserves order).
        """
        if not GPIOZERO_AVAILABLE:
            self._logger.debug(
                f"Rotary encoder '{encoder_name}' binding skipped (gpiozero unavailable)"
            )
            return

        if action_cw is None and action_ccw is None and not modifier_actions:
            self._logger.warning(
                f"Rotary encoder '{encoder_name}' has no CW or CCW action"
            )
            return

        if modifier_actions is None:
            modifier_actions = {}

        try:
            # Create RotaryEncoder instance
            encoder = RotaryEncoder(clk_pin, dt_pin)  # type: ignore

            # Helper function to get the appropriate CW action
            def get_cw_action() -> Callable[[], None] | None:
                for mod_name, (mod_cw, mod_ccw) in modifier_actions.items():
                    if self.is_modifier_active(mod_name) and mod_cw:
                        self._logger.debug(
                            f"Rotary encoder '{encoder_name}' CW using modifier '{mod_name}'"
                        )
                        return mod_cw
                return action_cw

            # Helper function to get the appropriate CCW action
            def get_ccw_action() -> Callable[[], None] | None:
                for mod_name, (mod_cw, mod_ccw) in modifier_actions.items():
                    if self.is_modifier_active(mod_name) and mod_ccw:
                        self._logger.debug(
                            f"Rotary encoder '{encoder_name}' CCW using modifier '{mod_name}'"
                        )
                        return mod_ccw
                return action_ccw

            # Bind rotation callbacks
            def on_rotate_cw() -> None:
                cw_action = get_cw_action()
                if cw_action:
                    self._logger.debug(f"Rotary encoder '{encoder_name}' rotated CW")
                    try:
                        cw_action()
                    except Exception as e:
                        self._logger.exception(
                            f"Rotary encoder '{encoder_name}' CW action failed: {e}"
                        )

            def on_rotate_ccw() -> None:
                ccw_action = get_ccw_action()
                if ccw_action:
                    self._logger.debug(f"Rotary encoder '{encoder_name}' rotated CCW")
                    try:
                        ccw_action()
                    except Exception as e:
                        self._logger.exception(
                            f"Rotary encoder '{encoder_name}' CCW action failed: {e}"
                        )

            encoder.when_rotated_clockwise = on_rotate_cw
            encoder.when_rotated_counter_clockwise = on_rotate_ccw

            self._rotary_encoders[encoder_name] = encoder
            self._logger.info(
                f"Rotary encoder '{encoder_name}' bound (CLK={clk_pin}, DT={dt_pin}, "
                f"CW={action_cw is not None}, CCW={action_ccw is not None}, "
                f"modifiers={len(modifier_actions)})"
            )

        except Exception as e:
            self._logger.error(
                f"Failed to bind rotary encoder '{encoder_name}' "
                f"(CLK={clk_pin}, DT={dt_pin}): {e}"
            )

    def unbind_all(self) -> None:
        """Release all GPIO pins and rotary encoders."""
        for pin, button in self._buttons.items():
            try:
                button.close()
                self._logger.debug(f"GPIO pin {pin} released")
            except Exception as e:
                self._logger.error(f"Failed to release GPIO pin {pin}: {e}")

        for encoder_name, encoder in self._rotary_encoders.items():
            try:
                encoder.close()
                self._logger.debug(f"Rotary encoder '{encoder_name}' released")
            except Exception as e:
                self._logger.error(
                    f"Failed to release rotary encoder '{encoder_name}': {e}"
                )

        self._buttons.clear()
        self._button_states.clear()
        self._rotary_encoders.clear()
        self._modifier_states.clear()

    def is_modifier_active(self, modifier_name: str) -> bool:
        """Check if a named modifier button is currently held down."""
        return self._modifier_states.get(modifier_name, False)

    def is_available(self) -> bool:
        """Check if GPIO functionality is available."""
        return GPIOZERO_AVAILABLE
