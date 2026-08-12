from __future__ import annotations

import time
from collections.abc import Sequence

import mido
import pytest

from h9control.protocol.codes import H9SysexCodes, H9SystemKeys
from h9control.protocol.h9_protocol import H9Protocol
from h9control.protocol.sysex import build_eventide_sysex


class FakeTransport:
    """In-memory transport that returns queued responses and records sends."""

    def __init__(self, device_id: int = 1) -> None:
        self.device_id = device_id
        self._responses: list[mido.Message] = []
        self.sent: list[list[int]] = []
        self.closed = False

    def queue_frame(self, command: int, payload: bytes = b"", device_id: int | None = None) -> None:
        data = build_eventide_sysex(
            device_id if device_id is not None else self.device_id, command, payload
        )
        self._responses.append(mido.Message("sysex", data=data[1:-1]))

    def close(self) -> None:
        self.closed = True

    def send_sysex(self, framed_or_unframed: Sequence[int] | bytes | bytearray) -> None:
        self.sent.append(list(framed_or_unframed))

    def receive_pending(self) -> list[mido.Message]:
        messages = self._responses
        self._responses = []
        return messages

    def send_program_change(self, program: int, channel: int = 0) -> None:
        raise NotImplementedError

    def send_control_change(self, control: int, value: int, channel: int = 0) -> None:
        raise NotImplementedError


@pytest.fixture()
def fake() -> FakeTransport:
    return FakeTransport()


@pytest.fixture()
def protocol(fake: FakeTransport) -> H9Protocol:
    return H9Protocol(fake, device_id=1)


def test_request_current_program_parses_dump(protocol: H9Protocol, fake: FakeTransport) -> None:
    dump = (
        "[1] 0 2 1\r\n0 2600 3600 4200 3000 5200 4800 3000 2400 4000 5000 7F\r\n"
        "C_SIM01\r\nDIGDLY\r\nPRISTINE DIGITAL\r\n\x00"
    ).encode("ascii")
    fake.queue_frame(H9SysexCodes.SYSEXC_TJ_PROGRAM_DUMP, dump)

    preset = protocol.request_current_program()

    assert preset.preset_number == 1
    assert preset.algorithm_key == "DIGDLY"
    assert preset.preset_name == "PRISTINE DIGITAL"
    assert len(fake.sent) == 1
    assert fake.sent[0][:5] == [0xF0, 0x1C, 0x70, 1, H9SysexCodes.SYSEXC_TJ_PROGRAM_WANT]


def test_request_current_program_times_out(protocol: H9Protocol) -> None:
    with pytest.raises(TimeoutError, match="0x4F"):
        protocol.request_current_program(timeout_s=0.05)


def test_get_value_parses_decimal_value(protocol: H9Protocol, fake: FakeTransport) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 12500")
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 12500


def test_get_value_parses_hex_value(protocol: H9Protocol, fake: FakeTransport) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 30D4")
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 12500


def test_get_value_ignores_wrong_key_then_matches(
    protocol: H9Protocol, fake: FakeTransport
) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"303 9999")
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 4200")
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 4200


def test_get_value_accepts_broadcast_and_own_device(
    protocol: H9Protocol, fake: FakeTransport
) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 1111", device_id=0)
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 1111


def test_get_value_ignores_other_device_ids(protocol: H9Protocol, fake: FakeTransport) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 1111", device_id=2)
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 2222")
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 2222


def test_get_value_times_out(protocol: H9Protocol) -> None:
    with pytest.raises(TimeoutError, match="302"):
        protocol.get_value(H9SystemKeys.KEY_SP_TEMPO, timeout_s=0.05)


def test_get_value_ignores_malformed_dumps(protocol: H9Protocol, fake: FakeTransport) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302")
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"\x00\x00\x00")
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 7777")
    assert protocol.get_value(H9SystemKeys.KEY_SP_TEMPO) == 7777


def test_get_current_bpm_divides_by_100(protocol: H9Protocol, fake: FakeTransport) -> None:
    fake.queue_frame(H9SysexCodes.SYSEXC_VALUE_DUMP, b"302 13840")
    assert protocol.get_current_bpm() == pytest.approx(138.4)


def test_set_parameter_builds_expected_payload(protocol: H9Protocol, fake: FakeTransport) -> None:
    protocol.set_parameter(H9SystemKeys.KEY_SP_TEMPO, 12500)

    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent[4] == H9SysexCodes.SYSEXC_VALUE_PUT
    assert bytes(sent[5:-1]) == b"302 12500"


def test_set_parameter_hex_string_value(protocol: H9Protocol, fake: FakeTransport) -> None:
    protocol.set_parameter(H9SystemKeys.KEY_SP_TEMPO, "30D4")
    sent = fake.sent[0]
    assert bytes(sent[5:-1]) == b"302 30D4"
