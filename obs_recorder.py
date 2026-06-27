"""
obs_recorder.py

Controls OBS Studio screen + webcam recording via WebSocket for Phase 1
sessions. Designed to be invoked by start_session.sh alongside the other
data stream scripts.

Requires:
  - OBS Studio (28+) running with WebSocket server enabled
  - A scene named "ThesisPhase1" pre-configured with screen + webcam sources
  - Credentials file at /Users/Shared/thesis-phase1/.obs_credentials (mode 600) containing:
      OBS_HOST=localhost
      OBS_PORT=4455
      OBS_PASSWORD=<your password>
  - obsws-python library installed in the venv

Usage:
    python obs_recorder.py --duration 300 --output /path/to/recording.mp4

Robustness notes (why this script is shaped the way it is):
  - The SIGTERM/SIGINT handler is registered BEFORE start_record(), so there
    is no window where a recording is running without a handler to stop it.
    A prior version registered the handler after start_record(), which could
    leave OBS recording forever if a signal arrived in that gap, breaking the
    NEXT session's StartRecord with a 500 (OBS already recording).
  - On connect, if OBS is already recording (leftover from a crashed prior
    session), we stop that stale recording before starting ours.
  - On stop, we poll OBS to confirm the recording actually ended and the file
    is finalized before moving it, rather than relying on a fixed sleep.
"""

import argparse
import os
import signal
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import obsws_python as obs
except ImportError:
    print("ERROR: obsws-python not installed. Run:")
    print("  pip install obsws-python")
    sys.exit(1)


def load_credentials(path):
    """Load OBS_HOST, OBS_PORT, OBS_PASSWORD from a KEY=VALUE file."""
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds


def is_recording(cl):
    """Return True if OBS currently has an active recording output."""
    try:
        return bool(cl.get_record_status().output_active)
    except Exception:
        return False


def stop_and_locate(cl):
    """Send stop_record, wait for OBS to report the recording inactive, and
    return the path OBS wrote (or None). Polls rather than fixed-sleeping so
    a slow finalize of a large file does not get cut short."""
    actual_path = None
    try:
        result = cl.stop_record()
        actual_path = getattr(result, "output_path", None)
    except Exception as e:
        print(f"  ERROR stopping recording: {e}")

    # Poll until OBS reports the output inactive (finalize complete), up to 20s.
    for _ in range(40):
        if not is_recording(cl):
            break
        time.sleep(0.5)

    # OBS sometimes returns the path on stop; if not, we cannot know it here.
    return actual_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration", type=int, default=300,
                        help="Recording duration in seconds")
    parser.add_argument("--output", type=str, required=True,
                        help="Final output MP4 path")
    parser.add_argument("--scene", type=str, default="ThesisPhase1",
                        help="OBS scene to activate before recording")
    parser.add_argument("--credentials", type=str, default=None,
                        help="Path to credentials file (default: /Users/Shared/thesis-phase1/.obs_credentials)")
    args = parser.parse_args()

    cred_path = args.credentials or "/Users/Shared/thesis-phase1/.obs_credentials"
    if not os.path.exists(cred_path):
        print(f"ERROR: credentials file not found at {cred_path}")
        print("Create it with:")
        print("  echo 'OBS_HOST=localhost' > /Users/Shared/thesis-phase1/.obs_credentials")
        print("  echo 'OBS_PORT=4455' >> /Users/Shared/thesis-phase1/.obs_credentials")
        print("  echo 'OBS_PASSWORD=<your_password>' >> /Users/Shared/thesis-phase1/.obs_credentials")
        print("  chmod 600 /Users/Shared/thesis-phase1/.obs_credentials")
        sys.exit(1)

    mode = os.stat(cred_path).st_mode & 0o777
    if mode & 0o077:
        print(f"WARNING: credentials file at {cred_path} has loose permissions ({oct(mode)})")
        print("Recommend: chmod 600 " + cred_path)

    creds = load_credentials(cred_path)
    host = creds.get("OBS_HOST", "localhost")
    port = int(creds.get("OBS_PORT", "4455"))
    password = creds.get("OBS_PASSWORD", "")

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("OBS recorder")
    print(f"  OBS at:    {host}:{port}")
    print(f"  Duration:  {args.duration}s")
    print(f"  Scene:     {args.scene}")
    print(f"  Output:    {output_path}")
    print()

    print("Connecting to OBS...")
    try:
        cl = obs.ReqClient(host=host, port=port, password=password, timeout=5)
    except Exception as e:
        print(f"ERROR connecting to OBS: {e}")
        print("Confirm: (1) OBS is running, (2) WebSocket Server enabled in Tools menu, "
              "(3) port and password match credentials file.")
        sys.exit(1)

    try:
        info = cl.get_version()
        print(f"  Connected. OBS version: {info.obs_version}, "
              f"WebSocket version: {info.obs_web_socket_version}")
    except Exception:
        print("  Connected (version info unavailable).")

    # GUARD: if OBS is already recording (leftover from a crashed prior
    # session), stop that stale recording first. Without this, our
    # start_record() below would fail with a 500 and this session would get
    # no video - exactly the cascade that one stuck session causes.
    if is_recording(cl):
        print("  NOTE: OBS was already recording (stale from a prior session). "
              "Stopping it before starting a clean recording.")
        stop_and_locate(cl)

    try:
        cl.set_current_program_scene(args.scene)
        print(f"  Active scene: {args.scene}")
    except Exception as e:
        print(f"  WARNING: could not switch to scene '{args.scene}': {e}")
        print(f"  Recording will proceed with whatever scene is currently active.")

    # Register the signal handler BEFORE starting the recording. This closes
    # the race where a signal arriving between start_record() and handler
    # registration would kill the process with OBS left recording.
    stop_requested = {"value": False}

    def handle_signal(sig, frame):
        print(f"\n  [signal {sig} received, will stop recording]")
        stop_requested["value"] = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    print("Starting recording...")
    start_dt = datetime.now(timezone.utc)
    start_iso = start_dt.isoformat()
    sidecar = output_path.parent / "recording_start.txt"
    try:
        sidecar.write_text(start_iso + "\n", encoding="utf-8")
        print(f"  Wrote anchor: {sidecar}")
    except OSError as e:
        print(f"  WARNING: could not write {sidecar}: {e}")

    try:
        cl.start_record()
        print(f"  Recording started at {start_iso}")
    except Exception as e:
        print(f"ERROR starting recording: {e}")
        # If start failed because OBS is somehow still recording, try once more
        # after stopping.
        if is_recording(cl):
            print("  OBS still reported recording; stopping and retrying once...")
            stop_and_locate(cl)
            try:
                cl.start_record()
                print(f"  Recording started on retry at "
                      f"{datetime.now(timezone.utc).isoformat()}")
            except Exception as e2:
                print(f"ERROR starting recording on retry: {e2}")
                sys.exit(1)
        else:
            sys.exit(1)

    # Wait for duration or until a signal requests early stop.
    start = time.monotonic()
    while time.monotonic() - start < args.duration and not stop_requested["value"]:
        time.sleep(0.5)

    print("Stopping recording...")
    end_iso = datetime.now(timezone.utc).isoformat()
    actual_obs_path = stop_and_locate(cl)
    print(f"  Recording stopped at {end_iso}")
    if actual_obs_path:
        print(f"  OBS wrote file to: {actual_obs_path}")

    # Move the recording to the requested output location.
    moved = False
    if actual_obs_path and Path(actual_obs_path).exists():
        try:
            shutil.move(actual_obs_path, str(output_path))
            print(f"  Moved recording to: {output_path}")
            print(f"  File size: {output_path.stat().st_size / (1024*1024):.1f} MB")
            moved = True
        except Exception as e:
            print(f"  WARNING: could not move recording to output path: {e}")
            print(f"  Recording remains at: {actual_obs_path}")

    if not moved:
        # Fallback: OBS didn't hand us the path (some versions return empty on
        # stop). Find the newest .mp4 in OBS's recordings folder modified since
        # we started, and move that. This keeps the recording from being
        # orphaned when stop_record() returns no path.
        print("  OBS did not return an output path; searching recordings folder...")
        rec_dir = Path("/Users/Shared/thesis-phase1/recordings")
        start_unix = start_dt.timestamp()
        candidates = []
        if rec_dir.is_dir():
            for mp4 in rec_dir.glob("*.mp4"):
                if mp4.stat().st_mtime >= start_unix - 5:
                    candidates.append((mp4.stat().st_mtime, mp4))
        if len(candidates) == 1:
            _, mp4 = candidates[0]
            try:
                shutil.move(str(mp4), str(output_path))
                print(f"  Recovered and moved: {mp4} -> {output_path}")
                print(f"  File size: {output_path.stat().st_size / (1024*1024):.1f} MB")
                moved = True
            except Exception as e:
                print(f"  WARNING: could not move recovered recording: {e}")
        elif len(candidates) > 1:
            print("  Multiple candidate recordings found; not moving automatically:")
            for mt, mp4 in sorted(candidates):
                print(f"    {datetime.utcfromtimestamp(mt)}Z  {mp4}")
            print("  Move the correct one manually, or run reslice_osquery.py.")
        else:
            print("  WARNING: no recording file located in OBS's recordings folder.")

    if not moved:
        print("  WARNING: recording.mp4 was not placed in the session dir. "
              "Recover with reslice_osquery.py.")

    print("OBS recorder complete.")


if __name__ == "__main__":
    main()
