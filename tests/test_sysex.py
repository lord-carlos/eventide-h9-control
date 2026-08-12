from __future__ import annotations

import pytest
import mido

from h9control.protocol.sysex import (
    EVENTIDE_MANUFACTURER_ID,
    EVENTIDE_MODEL_ID_H9,
    SysexFrame,
    build_eventide_sysex,
    decode_eventide_sysex,
    format_sysex_bytes,
)


def test_build_eventide_sysex_frames_message() -> None:
    msg = build_eventide_sysex(1, 0x4E, b"abc")
    assert msg == [0xF0, EVENTIDE_MANUFACTURER_ID, EVENTIDE_MODEL_ID_H9, 1, 0x4E, 0x61, 0x62, 0x63, 0xF7]


def test_build_eventide_sysex_empty_payload() -> None:
    msg = build_eventide_sysex(5, 0x4E)
    assert msg == [0xF0, 0x1C, 0x70, 5, 0x4E, 0xF7]


@pytest.mark.parametrize("device_id", [-1, 128])
def test_build_eventide_sysex_rejects_invalid_device_id(device_id: int) -> None:
    with pytest.raises(ValueError):
        build_eventide_sysex(device_id, 0x4E)


@pytest.mark.parametrize("command", [-1, 256])
def test_build_eventide_sysex_rejects_invalid_command(command: int) -> None:
    with pytest.raises(ValueError):
        build_eventide_sysex(1, command)


def test_decode_eventide_sysex_round_trip() -> None:
    msg = mido.Message("sysex", data=[0x1C, 0x70, 3, 0x2E, 0x33, 0x30, 0x32])
    frame = decode_eventide_sysex(msg)
    assert frame == SysexFrame(
        manufacturer_id=0x1C,
        model_id=0x70,
        device_id=3,
        command=0x2E,
        payload=b"302",
    )


def test_decode_eventide_sysex_returns_none_for_non_sysex() -> None:
    msg = mido.Message("note_on", note=60, velocity=64, channel=0)
    assert decode_eventide_sysex(msg) is None


def test_decode_eventide_sysex_returns_none_for_short_message() -> None:
    msg = mido.Message("sysex", data=[0x1C, 0x70, 1])
    assert decode_eventide_sysex(msg) is None


def test_decode_eventide_sysex_returns_none_for_wrong_manufacturer() -> None:
    msg = mido.Message("sysex", data=[0x00, 0x70, 1, 0x4F])
    assert decode_eventide_sysex(msg) is None


def test_decode_eventide_sysex_returns_none_for_wrong_model() -> None:
    msg = mido.Message("sysex", data=[0x1C, 0x71, 1, 0x4F])
    assert decode_eventide_sysex(msg) is None


def test_format_sysex_bytes_plain() -> None:
    assert format_sysex_bytes([0xF0, 0x1C, 0x70]) == "F0 1C 70"


def test_format_sysex_bytes_accepts_bytes_and_bytearray() -> None:
    assert format_sysex_bytes(bytes([0x1C, 0x70])) == "1C 70"
    assert format_sysex_bytes(bytearray([0x1C, 0x70])) == "1C 70"


def test_format_sysex_bytes_truncates() -> None:
    data = list(range(10))
    out = format_sysex_bytes(data, max_len=4)
    assert out == "00 01 02 03 …(+6 bytes)"


def test_format_sysex_bytes_no_truncation_at_exact_limit() -> None:
    data = [0x01, 0x02, 0x03, 0x04]
    assert format_sysex_bytes(data, max_len=4) == "01 02 03 04"
