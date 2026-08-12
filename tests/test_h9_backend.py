from __future__ import annotations

from collections.abc import Callable

import pytest

from h9control.app.h9_backend import H9Backend
from h9control.protocol.codes import H9SysexCodes, H9SystemKeys
from h9control.protocol.sysex import SysexFrame


def _frame(command: int, payload: bytes, device_id: int = 1) -> SysexFrame:
    return SysexFrame(
        manufacturer_id=0x1C,
        model_id=0x70,
        device_id=device_id,
        command=command,
        payload=payload,
    )


class FakeWaitLoop:
    """Feeds frames to a predicate; mimics the worker's wait mechanism."""

    def __init__(self, frames: list[SysexFrame]) -> None:
        self._frames = frames
        self.sent: list[tuple[int, bytes]] = []
        self.predicates_seen: list[Callable[[SysexFrame], bool]] = []

    def send(self, command: int, payload: bytes) -> None:
        self.sent.append((command, payload))

    def wait(
        self, predicate: Callable[[SysexFrame], bool], timeout_s: float
    ) -> SysexFrame:
        self.predicates_seen.append(predicate)
        for frame in self._frames:
            if predicate(frame):
                return frame
        raise TimeoutError(f"Timed out (timeout_s={timeout_s})")


def _backend(loop: FakeWaitLoop) -> H9Backend:
    return H9Backend(send_eventide=loop.send, wait_for_frame=loop.wait)


def test_get_bpm_requests_tempo_and_divides() -> None:
    loop = FakeWaitLoop([_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 12500")])
    backend = _backend(loop)

    assert backend.get_bpm(timeout_s=1.0) == 125.0
    assert loop.sent == [(H9SysexCodes.SYSEXC_VALUE_WANT, b"302")]


def test_set_bpm_sends_hex_value() -> None:
    loop = FakeWaitLoop([])
    backend = _backend(loop)

    backend.set_bpm(125)
    assert loop.sent == [(H9SysexCodes.SYSEXC_VALUE_PUT, b"302 30D4")]


def test_get_value_parses_decimal_and_hex() -> None:
    loop = FakeWaitLoop([_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 30D4")])
    assert _backend(loop).get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=1.0) == 12500

    loop = FakeWaitLoop([_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 12500")])
    assert _backend(loop).get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=1.0) == 12500


def test_get_value_skips_non_matching_frames() -> None:
    loop = FakeWaitLoop(
        [
            _frame(H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP, b"junk"),
            _frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"303 9999"),
            _frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 1234"),
        ]
    )
    assert _backend(loop).get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=1.0) == 1234


def test_get_value_rejects_malformed_payload() -> None:
    loop = FakeWaitLoop([_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302")])
    with pytest.raises(ValueError, match="Unexpected VALUE_DUMP payload"):
        _backend(loop).get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=1.0)


def test_wait_timeout_propagates() -> None:
    loop = FakeWaitLoop([])
    with pytest.raises(TimeoutError):
        _backend(loop).get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=0.05)


def test_predicate_matches_only_tempo_key() -> None:
    loop = FakeWaitLoop([_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 12500")])
    backend = _backend(loop)
    backend.get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=1.0)

    predicate = loop.predicates_seen[0]
    assert predicate(_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 42"))
    assert not predicate(_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"303 42"))
    assert not predicate(_frame(H9SysexCodes.SYSEXC_PROGRAM_DUMP, b"302 42"))
