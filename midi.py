"""Compatibility shim for the move of the MIDI transport.

`H9Midi` and `MidiPorts` now live in `h9control.transport.h9_midi`.
This module keeps the historical root-level import paths working:

    from midi import H9Midi, MidiPorts
"""

from h9control.transport.h9_midi import *  # noqa: F403
from h9control.transport.h9_midi import H9Midi, MidiPorts  # noqa: F401
