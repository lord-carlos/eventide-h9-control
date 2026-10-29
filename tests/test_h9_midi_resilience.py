"""Resilience tests for the MIDI transport lifecycle.

Everything here runs against a fake `mido` module, so no real MIDI hardware
or ports are touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from h9control.transport.h9_midi import H9Midi
from h9control.transport.midi_transport import MidiTransport
from h9control.transport.simulated_h9 import SimulatedH9Transport

DEVICE_PREFIX = "H9 Pedal"


class FakePort:
    """Records close() calls and can simulate a close failure."""

    def __init__(self, name: str, *, fail_on_close: bool = False) -> None:
        self.name = name
        self.close_calls = 0
        self._fail_on_close = fail_on_close

    def close(self) -> None:
        self.close_calls += 1
        if self._fail_on_close:
            raise OSError(f"close failed: {self.name}")


class FakeMido:
    """Minimal stand-in for the `mido` module used by `H9Midi`."""

    def __init__(
        self,
        outputs: Sequence[str] = (DEVICE_PREFIX,),
        inputs: Sequence[str] = (DEVICE_PREFIX,),
    ) -> None:
        self.output_names = list(outputs)
        self.input_names = list(inputs)
        self.opened_outputs: list[FakePort] = []
        self.opened_inputs: list[FakePort] = []
        self.backend: str | None = None
        self.fail_open_input = False
        self.fail_on_close = False
        self.fail_enumeration = False

    def set_backend(self, backend: str) -> None:
        self.backend = backend

    def get_output_names(self) -> list[str]:
        if self.fail_enumeration:
            raise OSError("enumeration failed")
        return list(self.output_names)

    def get_input_names(self) -> list[str]:
        return list(self.input_names)

    def open_output(self, name: str) -> FakePort:
        port = FakePort(name, fail_on_close=self.fail_on_close)
        self.opened_outputs.append(port)
        return port

    def open_input(self, name: str) -> FakePort:
        if self.fail_open_input:
            raise OSError("input open failed")
        port = FakePort(name, fail_on_close=self.fail_on_close)
        self.opened_inputs.append(port)
        return port


def _midi(
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeMido,
    *,
    input_enabled: bool = True,
) -> H9Midi:
    monkeypatch.setattr("h9control.transport.h9_midi.mido", fake)
    return H9Midi(device_prefix=DEVICE_PREFIX, input_enabled=input_enabled)


def test_connect_returns_output_name(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMido(outputs=("Unrelated", DEVICE_PREFIX))
    midi = _midi(monkeypatch, fake)

    assert midi.connect() == DEVICE_PREFIX
    assert fake.opened_outputs[-1].name == DEVICE_PREFIX


def test_connect_without_matching_output_keeps_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido(outputs=("Unrelated",))
    midi = _midi(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="No MIDI output port starts with"):
        midi.connect()
    assert fake.opened_outputs == []


def test_close_closes_both_handles_when_input_close_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    fake.fail_on_close = True
    midi = _midi(monkeypatch, fake)
    midi.connect()

    midi.close()  # must not raise

    output, input_ = fake.opened_outputs[0], fake.opened_inputs[0]
    assert output.close_calls == 1
    assert input_.close_calls == 1
    assert midi._in is None
    assert midi._out is None


def test_close_resets_handles_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    midi = _midi(monkeypatch, fake)
    midi.connect()

    midi.close()
    assert midi._in is None
    assert midi._out is None

    output, input_ = fake.opened_outputs[0], fake.opened_inputs[0]
    midi.close()  # second close is a no-op

    assert output.close_calls == 1
    assert input_.close_calls == 1
    assert midi._in is None
    assert midi._out is None


def test_close_without_connect_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    midi = _midi(monkeypatch, FakeMido())

    midi.close()

    assert midi._in is None
    assert midi._out is None


def test_connect_twice_closes_previous_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    midi = _midi(monkeypatch, fake)

    midi.connect()
    midi.connect()

    assert len(fake.opened_outputs) == 2
    assert len(fake.opened_inputs) == 2
    assert fake.opened_outputs[0].close_calls == 1
    assert fake.opened_inputs[0].close_calls == 1
    assert fake.opened_outputs[1].close_calls == 0
    assert fake.opened_inputs[1].close_calls == 0
    assert midi._out is fake.opened_outputs[1]
    assert midi._in is fake.opened_inputs[1]


def test_input_open_failure_closes_output_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    fake.fail_open_input = True
    midi = _midi(monkeypatch, fake)

    with pytest.raises(OSError, match="input open failed"):
        midi.connect()

    assert len(fake.opened_outputs) == 1
    assert fake.opened_outputs[0].close_calls == 1
    assert midi._in is None
    assert midi._out is None


def test_input_open_failure_reraises_even_if_close_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    fake.fail_open_input = True
    fake.fail_on_close = True
    midi = _midi(monkeypatch, fake)

    with pytest.raises(OSError, match="input open failed"):
        midi.connect()

    assert midi._in is None
    assert midi._out is None


def test_is_port_present_matches_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    midi = _midi(monkeypatch, FakeMido(outputs=("Unrelated", DEVICE_PREFIX)))

    assert midi.is_port_present() is True


def test_is_port_present_without_matching_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    midi = _midi(monkeypatch, FakeMido(outputs=("Other",)))

    assert midi.is_port_present() is False


def test_is_port_present_returns_false_when_enumeration_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido()
    fake.fail_enumeration = True
    midi = _midi(monkeypatch, fake)

    assert midi.is_port_present() is False


def test_is_port_present_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMido(outputs=("Other",))
    midi = _midi(monkeypatch, fake)
    assert midi.is_port_present() is False

    fake.output_names = [DEVICE_PREFIX]
    assert midi.is_port_present() is True


def test_midi_transport_delegates_is_port_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMido(outputs=(DEVICE_PREFIX,))
    transport = MidiTransport(_midi(monkeypatch, fake))

    assert transport.is_port_present() is True


def test_simulated_transport_is_always_present() -> None:
    transport = SimulatedH9Transport()

    assert transport.is_port_present() is True
    transport.close()
    assert transport.is_port_present() is True
