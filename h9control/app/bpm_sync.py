"""BPM adjustment and live-tempo sync logic for the H9 dashboard worker.

Extracted from ``qt_worker.py`` as a mixin so ``H9DeviceWorker`` keeps its
public signal/slot interface unchanged. The host worker supplies the
hardware-facing collaborators (``_backend`` / ``_transport`` / ``_connect`` /
``_refresh_state``) and owns the BPM bookkeeping attributes; this module
only contains the tempo logic itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6 import QtCore

from h9control.app.config import ConfigManager
from h9control.app.h9_backend import H9Backend
from h9control.app.state import DashboardState
from h9control.transport.midi_transport import H9Transport

BPM_MIN = 20.0
BPM_MAX = 300.0


def clamp_bpm(value: float) -> int:
    """Round a BPM and clamp it to the H9's valid tempo range."""
    return int(max(BPM_MIN, min(BPM_MAX, round(value))))


def sanitize_live_bpm(bpm: float | None) -> float | None:
    """Return a usable live (beat-detected) BPM, or None when unavailable.

    The beat detector emits ``-1.0`` as a "stream died" sentinel; any
    non-positive value must be treated as "no live BPM available" so the
    dashboard never renders it and auto-sync never clamps it up to BPM 20
    and sends it to the pedal.
    """
    if bpm is None or bpm <= 0:
        return None
    return bpm


class BpmSyncMixin:
    """BPM state and device-sync behavior mixed into the H9 worker.

    Expects the host object to provide:

    - attributes: ``_logger``, ``_config``, ``_backend``, ``_transport``,
      ``_last_state``, ``_live_bpm``, ``_last_good_bpm``,
      ``_last_sent_auto_bpm``
    - methods: ``_connect()``, ``_refresh_state()``
    """

    # Provided by the host worker.
    _logger: logging.Logger
    _config: ConfigManager
    _backend: H9Backend
    _transport: H9Transport | None
    _last_state: DashboardState
    _live_bpm: float | None
    _last_good_bpm: float | None
    _last_sent_auto_bpm: int | None
    _connect: Callable[[], None]
    _refresh_state: Callable[[], None]

    @QtCore.Slot(int)
    def adjust_bpm(self, delta_bpm: int) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        bpm_now = self._sanitize_bpm(self._last_state.bpm)
        if bpm_now is None:
            return

        target = clamp_bpm(bpm_now + int(delta_bpm))

        try:
            self._backend.set_bpm(target)
            # Remember what we just sent so continuous auto-sync does not
            # immediately clobber this manual value with the live BPM.
            self._last_sent_auto_bpm = target
        except Exception:
            self._logger.exception("Failed to set BPM")
            return

        # Re-read state so UI stays in sync with the pedal.
        self._refresh_state()

    @QtCore.Slot()
    def sync_live_bpm(self) -> None:
        if self._transport is None:
            self._connect()
        if self._transport is None:
            return

        if self._live_bpm is None:
            return

        target = clamp_bpm(self._live_bpm)

        try:
            self._backend.set_bpm(target)
            self._last_sent_auto_bpm = target
        except Exception:
            self._logger.exception("Failed to set BPM")
            return

        # Re-read state so UI stays in sync with the pedal.
        self._refresh_state()

    def _check_auto_bpm_sync(self) -> int | None:
        """Check if auto BPM sync should fire and send BPM if needed.

        Returns the newly sent BPM value if successful, None otherwise.
        """
        if self._config.auto_bpm_mode != "continuous":
            self._logger.debug(f"Auto BPM sync not in continuous mode, skipping")
            return None

        if self._live_bpm is None:
            self._logger.debug(f"No live BPM available, skipping auto-sync")
            return None

        if self._transport is None:
            self._logger.debug(f"Not connected, skipping auto-sync")
            return None

        rounded_bpm = int(round(self._live_bpm))

        # Only send if the rounded value has changed
        if rounded_bpm == self._last_sent_auto_bpm:
            self._logger.debug(
                f"Auto BPM sync: rounded BPM {rounded_bpm} unchanged, skipping"
            )
            return None

        target = clamp_bpm(rounded_bpm)

        try:
            self._backend.set_bpm(target)
            self._last_sent_auto_bpm = target
            self._logger.info(f"Auto-synced BPM: {target}")
            return target
        except Exception:
            self._logger.exception("Failed to auto-sync BPM")
            return None

    def _sanitize_bpm(self, bpm: float | None) -> float | None:
        if bpm is None:
            return self._last_good_bpm

        # Guardrail: we have observed occasional bogus tempo reads.
        if bpm < BPM_MIN or bpm > BPM_MAX:
            self._logger.debug(
                "Ignoring implausible BPM reading: %s (last_good=%s)",
                bpm,
                self._last_good_bpm,
            )
            return self._last_good_bpm

        self._last_good_bpm = bpm
        return bpm
