"""
nsworkspace_monitor.py

macOS focused-application monitor using NSWorkspace notifications via PyObjC.

osquery on macOS cannot tell us which application is currently focused (the
running_apps table's is_active column is hidden and not queryable). This
script fills that gap by subscribing to NSWorkspaceDidActivateApplicationNotification,
which fires every time the user switches to a different application.

What is captured:
  - Every app activation (becoming frontmost)
  - App bundle identifier, name, process ID
  - Sub-second UTC timestamp

What is NOT captured:
  - Window contents
  - Within-app activity
  - Anything outside app focus changes

Output format (JSONL, one record per line):
  {
    "timestamp_utc": "2026-05-13T10:23:45.123456+00:00",
    "type": "app_activation",
    "bundle_identifier": "com.microsoft.Word",
    "bundle_name": "Microsoft Word",
    "process_id": 12345,
    "launch_date": "2026-05-13T10:20:00.000000+00:00"
  }

Usage:
    python nsworkspace_monitor.py                          # 2 min default
    python nsworkspace_monitor.py --duration 1800          # 30 min
    python nsworkspace_monitor.py --output session.jsonl

macOS dependencies: PyObjC is already installed as a transitive
dependency of bleak. No additional install needed.
"""

import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from AppKit import NSWorkspace
from Foundation import NSObject, NSNotificationCenter, NSDate
from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode
from PyObjCTools import AppHelper


class AppActivationObserver(NSObject):
    """Subscribes to NSWorkspace activation notifications and writes JSONL."""

    def init_with_output(self, output_path):
        self = self.init()
        if self is None:
            return None
        self._output_path = output_path
        self._output_file = output_path.open("a")
        self._lock = threading.Lock()
        self._activation_count = 0
        return self

    def appActivated_(self, notification):
        """Called by NSWorkspace whenever the frontmost application changes."""
        try:
            user_info = notification.userInfo()
            app = user_info.objectForKey_("NSWorkspaceApplicationKey")
            if app is None:
                return

            ts = datetime.now(timezone.utc).isoformat()
            bundle_id = str(app.bundleIdentifier() or "")
            bundle_name = str(app.localizedName() or "")
            pid = int(app.processIdentifier())

            launch_date = app.launchDate()
            launch_iso = None
            if launch_date:
                # NSDate -> Unix timestamp -> UTC ISO
                launch_unix = launch_date.timeIntervalSince1970()
                launch_iso = datetime.fromtimestamp(
                    launch_unix, tz=timezone.utc
                ).isoformat()

            record = {
                "timestamp_utc": ts,
                "type": "app_activation",
                "bundle_identifier": bundle_id,
                "bundle_name": bundle_name,
                "process_id": pid,
                "launch_date": launch_iso,
            }

            with self._lock:
                self._output_file.write(json.dumps(record) + "\n")
                self._output_file.flush()
                self._activation_count += 1

            print(f"  [{ts}] -> {bundle_name} ({bundle_id})")
        except Exception as e:
            print(f"  [error in callback] {e}", file=sys.stderr)

    def closeFile(self):
        with self._lock:
            self._output_file.close()

    def count(self):
        return self._activation_count


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration", type=int, default=120,
                        help="Capture duration in seconds (default 120)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL path (default: timestamped file)")
    args = parser.parse_args()

    if args.output:
        output_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path(f"focused_app_{stamp}.jsonl")

    print(f"NSWorkspace focused-app monitor")
    print(f"Duration: {args.duration}s")
    print(f"Output:   {output_path}")
    print()

    # Set up observer
    observer = AppActivationObserver.alloc().init_with_output(output_path)
    if observer is None:
        print("Failed to create observer", file=sys.stderr)
        sys.exit(1)

    # Subscribe to the workspace notification center
    workspace = NSWorkspace.sharedWorkspace()
    notification_center = workspace.notificationCenter()
    notification_center.addObserver_selector_name_object_(
        observer,
        "appActivated:",
        "NSWorkspaceDidActivateApplicationNotification",
        None,
    )

    # Log the currently active app at startup so we have a starting state
    current = workspace.frontmostApplication()
    if current is not None:
        ts = datetime.now(timezone.utc).isoformat()
        record = {
            "timestamp_utc": ts,
            "type": "session_start_state",
            "bundle_identifier": str(current.bundleIdentifier() or ""),
            "bundle_name": str(current.localizedName() or ""),
            "process_id": int(current.processIdentifier()),
            "launch_date": None,
        }
        with output_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"  [start] currently active: {current.localizedName()}")

    print("Listening for app activations. Switch apps to test. Ctrl-C to stop early.\n")

    # Drive the NSNotification run loop with a manual CFRunLoopRunInMode poll.
    # AppHelper.runConsoleEventLoop() restarts the CF run loop internally
    # each iteration, so CFRunLoopStop / stopEventLoop called from a timer
    # thread does not reliably terminate it. Polling in 0.5-second slices
    # avoids that entirely: we check a stop flag and the deadline on each
    # slice, which is responsive within ~0.5s regardless of idle load.
    _stop = threading.Event()

    def handle_signal(signum, frame):
        name = "Ctrl-C" if signum == signal.SIGINT else "SIGTERM"
        print(f"\n[{name} received, stopping...]")
        _stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    deadline = time.monotonic() + args.duration
    while not _stop.is_set() and time.monotonic() < deadline:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.5, False)

    notification_center.removeObserver_(observer)
    observer.closeFile()

    print(f"\nSession complete.")
    print(f"  Activations captured: {observer.count()}")
    print(f"  Output:               {output_path}")


if __name__ == "__main__":
    main()
