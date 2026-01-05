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
    from gpiozero import Button  # type: ignore
    GPIOZERO_AVAILABLE = True
except ImportError:
    GPIOZERO_AVAILABLE = False
    Button = None


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
    ) -> None:
        """Bind tap and/or hold actions to a GPIO pin.
        
        Args:
            pin: BCM pin number (e.g., 17, 27, 22)
            tap_action: Callback for tap event (short press)
            hold_action: Callback for hold event (long press)
            pull_up: True for pull-up resistor, False for pull-down
            debounce_ms: Debounce time in milliseconds
            hold_threshold_ms: Time in ms to distinguish tap from hold
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
                self._logger.debug(f"GPIO pin {pin} pressed")

            # When released: check if tap or already fired hold
            def on_release() -> None:
                if state.press_time is None:
                    return
                
                hold_duration_ms = (time.time() - state.press_time) * 1000
                self._logger.debug(
                    f"GPIO pin {pin} released after {hold_duration_ms:.0f}ms"
                )
                
                # If hold already fired, ignore release
                if state.hold_fired:
                    state.press_time = None
                    return
                
                # Check if it was a tap (short press)
                if hold_duration_ms < hold_threshold_ms:
                    if tap_action:
                        self._logger.info(f"GPIO pin {pin} tap action triggered")
                        tap_action()
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

    def unbind_all(self) -> None:
        """Release all GPIO pins."""
        for pin, button in self._buttons.items():
            try:
                button.close()
                self._logger.debug(f"GPIO pin {pin} released")
            except Exception as e:
                self._logger.error(f"Failed to release GPIO pin {pin}: {e}")
        
        self._buttons.clear()
        self._button_states.clear()

    def is_available(self) -> bool:
        """Check if GPIO functionality is available."""
        return GPIOZERO_AVAILABLE
