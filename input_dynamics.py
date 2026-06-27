"""
input_dynamics.py

Capture mouse and keyboard timing to JSONL with sub-second UTC timestamps.
Configurable: enable/disable continuous mouse movement capture.

What is captured:
  - Keystroke timing (press / release) — NO content, only timing
  - Mouse clicks (left / right / middle, press / release)
  - Mouse movement at ~10Hz (optional)

What is NOT captured:
  - Actual keys pressed or text typed (privacy-critical)
  - Screen content
  - Window contents

Output format (JSONL, one record per line):
  {
    "timestamp_utc": "2026-05-11T20:15:23.123456+00:00",
    "type": "key_press",
    "key_class": "alpha" | "modifier" | "navigation" | "function" | "other"
  }
  {
    "timestamp_utc": "...",
    "type": "mouse_click",
    "button": "left" | "right" | "middle",
    "pressed": true | false,
    "x": 423, "y": 567
  }
  {
    "timestamp_utc": "...",
    "type": "mouse_move",
    "x": 423, "y": 567
  }

Usage:
    python input_dynamics.py                          # 2 min default, no mouse movement
    python input_dynamics.py --duration 1800          # 30 min capture
    python input_dynamics.py --duration 1800 --track-mouse-movement
    python input_dynamics.py --output session.jsonl

macOS note: requires Accessibility permission for the Terminal/Python.
First run will prompt — click Allow. If you miss it, fix in:
System Settings > Privacy & Security > Accessibility > add Terminal.
"""

import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pynput import keyboard, mouse


# Sample rate for continuous mouse movement (Hz)
MOUSE_MOVE_SAMPLE_HZ = 10


def classify_key(key) -> str:
    """
    Return a class label for a key — but NEVER the actual character.
    This is the privacy boundary: we record timing and key category only.
    """
    # Special keys (Key.shift, Key.cmd, Key.enter, etc.)
    if isinstance(key, keyboard.Key):
        name = key.name
        modifiers = {"shift", "shift_r", "ctrl", "ctrl_r", "alt", "alt_r",
                     "cmd", "cmd_r", "caps_lock"}
        navigation = {"left", "right", "up", "down", "page_up", "page_down",
                      "home", "end", "backspace", "delete", "tab", "enter",
                      "esc", "space"}
        function = {f"f{i}" for i in range(1, 25)}

        if name in modifiers:
            return "modifier"
        if name in navigation:
            return "navigation"
        if name in function:
            return "function"
        return "other"

    # Regular character keys — return only the class, not the character
    if isinstance(key, keyboard.KeyCode):
        return "alpha"

    return "other"


class InputCapture:
    """Coordinates the three listeners and writes events to JSONL."""

    def __init__(self, output_path: Path, track_mouse_movement: bool):
        self.output_path = output_path
        self.track_mouse_movement = track_mouse_movement
        self.output_file = output_path.open("a")
        self.lock = threading.Lock()

        # Counters for end-of-session summary
        self.keystroke_count = 0
        self.click_count = 0
        self.move_count = 0

        # Rate-limit mouse movement to MOUSE_MOVE_SAMPLE_HZ
        self._last_move_emit = 0.0
        self._move_interval = 1.0 / MOUSE_MOVE_SAMPLE_HZ

    def write(self, record: dict):
        with self.lock:
            self.output_file.write(json.dumps(record) + "\n")
            self.output_file.flush()

    def on_key_press(self, key):
        self.write({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "type": "key_press",
            "key_class": classify_key(key),
        })
        self.keystroke_count += 1

    def on_key_release(self, key):
        self.write({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "type": "key_release",
            "key_class": classify_key(key),
        })

    def on_click(self, x, y, button, pressed):
        self.write({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "type": "mouse_click",
            "button": button.name,
            "pressed": pressed,
            "x": int(x),
            "y": int(y),
        })
        if pressed:
            self.click_count += 1

    def on_move(self, x, y):
        if not self.track_mouse_movement:
            return
        now = time.monotonic()
        if now - self._last_move_emit < self._move_interval:
            return
        self._last_move_emit = now
        self.write({
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "type": "mouse_move",
            "x": int(x),
            "y": int(y),
        })
        self.move_count += 1

    def close(self):
        self.output_file.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration", type=int, default=120,
                        help="Capture duration in seconds (default 120)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL path (default: timestamped file)")
    parser.add_argument("--track-mouse-movement", action="store_true",
                        help="Capture continuous mouse movement at 10Hz "
                             "(off by default; produces ~36k records over 30min)")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path(f"input_{stamp}.jsonl")

    capture = InputCapture(output_path, args.track_mouse_movement)

    print(f"Capture duration: {args.duration}s")
    print(f"Output: {output_path}")
    print(f"Mouse movement tracking: "
          f"{'ENABLED at 10Hz' if args.track_mouse_movement else 'disabled'}")
    print(f"Keystroke content: NOT captured (timing + class only)")
    print()

    # Start listeners
    keyboard_listener = keyboard.Listener(
        on_press=capture.on_key_press,
        on_release=capture.on_key_release,
    )
    mouse_listener = mouse.Listener(
        on_move=capture.on_move,
        on_click=capture.on_click,
    )

    keyboard_listener.start()
    mouse_listener.start()

    print("Listeners running. Press Ctrl-C to stop early.\n")

    # Periodic status updates so the user knows it's alive
    start = time.monotonic()
    try:
        while time.monotonic() - start < args.duration:
            time.sleep(10)
            elapsed = int(time.monotonic() - start)
            print(f"  [{elapsed:>4}s]  keys: {capture.keystroke_count:>5}  "
                  f"clicks: {capture.click_count:>4}  "
                  f"moves: {capture.move_count:>6}")
    except KeyboardInterrupt:
        print("\n[Ctrl-C received, stopping...]")

    keyboard_listener.stop()
    mouse_listener.stop()
    capture.close()

    elapsed = time.monotonic() - start
    print(f"\nSession complete.")
    print(f"  Duration:           {elapsed:.0f} seconds")
    print(f"  Keystrokes (press): {capture.keystroke_count}")
    print(f"  Mouse clicks:       {capture.click_count}")
    print(f"  Mouse moves logged: {capture.move_count}")
    print(f"  Output:             {output_path}")


if __name__ == "__main__":
    main()
