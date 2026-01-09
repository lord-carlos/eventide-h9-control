#!/usr/bin/env python3
"""
Interactive test script to find the exact MIDI CC values where
the H9 TimeFactor delay time switches between note divisions.

Usage:
    uv run python scripts/test_delay_cc.py

Controls:
    UP/DOWN or +/- : Step CC value by 1
    LEFT/RIGHT     : Step CC value by 10
    q              : Quit
    r              : Reset to 0
    m              : Jump to middle (64)
    
The script sends CC #24 (Delay A time) on channel 0.
Watch the H9 display and note when it changes division.
"""

from __future__ import annotations

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from midi import H9Midi
from h9control.transport.midi_transport import MidiTransport


# H9 CC numbers for delay time
CC_DELAY_A = 24  # Knob 1 (DLY-A on TimeFactor algorithms)
CC_DELAY_B = 25  # Knob 2 (DLY-B on TimeFactor algorithms)

# Which CC to test (change if needed)
TEST_CC = CC_DELAY_A
TEST_CHANNEL = 0


def main() -> None:
    print("=" * 60)
    print("H9 Delay CC Value Finder")
    print("=" * 60)
    print(f"Testing CC #{TEST_CC} on channel {TEST_CHANNEL}")
    print()
    print("Controls:")
    print("  UP/DOWN or +/-  : Step by 1")
    print("  LEFT/RIGHT      : Step by 10")
    print("  r               : Reset to 0")
    print("  m               : Jump to middle (64)")
    print("  q               : Quit")
    print()
    
    # Connect to H9
    print("Connecting to H9...")
    midi = H9Midi(device_prefix="H9 Pedal")
    transport = MidiTransport(midi)
    
    try:
        info = transport.connect()
        print(f"Connected: {info.output_name}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        print("\nMake sure H9 is connected and powered on.")
        return
    
    current_value = 0
    
    # Send initial value
    transport.send_control_change(TEST_CC, current_value, TEST_CHANNEL)
    print(f"\n>>> CC value: {current_value:3d}  ({current_value / 127 * 100:.1f}%)")
    
    try:
        # Use msvcrt on Windows for key detection
        if sys.platform == "win32":
            import msvcrt
            
            while True:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    
                    # Handle arrow keys (they come as two bytes on Windows)
                    if key == b'\xe0' or key == b'\x00':
                        key = msvcrt.getch()
                        if key == b'H':  # Up
                            current_value = min(127, current_value + 1)
                        elif key == b'P':  # Down
                            current_value = max(0, current_value - 1)
                        elif key == b'M':  # Right
                            current_value = min(127, current_value + 10)
                        elif key == b'K':  # Left
                            current_value = max(0, current_value - 10)
                    elif key == b'+' or key == b'=':
                        current_value = min(127, current_value + 1)
                    elif key == b'-' or key == b'_':
                        current_value = max(0, current_value - 1)
                    elif key == b'r' or key == b'R':
                        current_value = 0
                    elif key == b'm' or key == b'M':
                        current_value = 64
                    elif key == b'q' or key == b'Q' or key == b'\x1b':  # q or Escape
                        print("\nQuitting...")
                        break
                    else:
                        continue
                    
                    transport.send_control_change(TEST_CC, current_value, TEST_CHANNEL)
                    print(f">>> CC value: {current_value:3d}  ({current_value / 127 * 100:.1f}%)")
        else:
            # Unix-like systems
            import tty
            import termios
            
            old_settings = termios.tcgetattr(sys.stdin)
            try:
                tty.setraw(sys.stdin.fileno())
                
                while True:
                    char = sys.stdin.read(1)
                    
                    if char == '\x1b':  # Escape sequence
                        next_chars = sys.stdin.read(2)
                        if next_chars == '[A':  # Up
                            current_value = min(127, current_value + 1)
                        elif next_chars == '[B':  # Down
                            current_value = max(0, current_value - 1)
                        elif next_chars == '[C':  # Right
                            current_value = min(127, current_value + 10)
                        elif next_chars == '[D':  # Left
                            current_value = max(0, current_value - 10)
                        else:
                            print("\nQuitting...")
                            break
                    elif char == '+' or char == '=':
                        current_value = min(127, current_value + 1)
                    elif char == '-' or char == '_':
                        current_value = max(0, current_value - 1)
                    elif char == 'r' or char == 'R':
                        current_value = 0
                    elif char == 'm' or char == 'M':
                        current_value = 64
                    elif char == 'q' or char == 'Q':
                        print("\nQuitting...")
                        break
                    else:
                        continue
                    
                    transport.send_control_change(TEST_CC, current_value, TEST_CHANNEL)
                    print(f"\r>>> CC value: {current_value:3d}  ({current_value / 127 * 100:.1f}%)   ", end='')
            finally:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
    
    finally:
        transport.close()
        print("Disconnected.")


if __name__ == "__main__":
    main()
