#!/usr/bin/env python3
"""
preflight_check.py - actively verify every stream is healthy BEFORE a session.

The session controller's built-in equipment check is necessary but not
sufficient: it confirms osqueryd is *running* and OBS is *reachable*, but a
running osqueryd can still be writing nothing (wrong config, stale log path,
permission change), and a reachable OBS can still fail StartRecord. This
script goes further - it watches each data source actually produce data for a
few seconds, so an empty stream is caught here instead of at the end of a
wasted 26-minute participant session.

Run it right before each session (the operator does this, participant not yet
in the room):

    cd $THESIS_DIR
    source .venv/bin/activate
    python preflight_check.py

Exit code 0 = all green. Non-zero = at least one stream is not healthy.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

THESIS_DIR = Path(os.environ.get("THESIS_DIR", Path(__file__).resolve().parent))
OSQUERY_LOG = THESIS_DIR / "osquery_logs" / "osqueryd.results.log"
CREDS = THESIS_DIR / ".obs_credentials"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD = "\033[1m"


def ok(label, msg):
    print(f"  {GREEN}[ OK ]{RESET} {label}: {msg}")
    return True


def fail(label, msg):
    print(f"  {RED}[FAIL]{RESET} {label}: {msg}")
    return False


def warn(label, msg):
    print(f"  {YELLOW}[WARN]{RESET} {label}: {msg}")
    return True


def load_creds():
    creds = {}
    if not CREDS.exists():
        return creds
    for line in CREDS.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds


def check_osquery():
    """osqueryd must be running AND have written to the global log recently.

    Previous approach probed by writing files into THESIS_DIR and waiting for
    file_events to appear, but the osquery conf explicitly excludes that path
    ($THESIS_DIR/%) so the probe never fired. Instead we read
    the tail of the log and verify a recent unixTime: running_apps_snapshot
    fires every 5s, so a healthy daemon will always have an entry < 90s old.
    """
    label = "osquery"
    # Running?
    r = subprocess.run(["pgrep", "-x", "osqueryd"], capture_output=True, text=True)
    if r.returncode != 0:
        return fail(label, "osqueryd not running. Start it: "
                            "sudo launchctl kickstart -k system/io.osquery.agent")

    if not OSQUERY_LOG.exists():
        return fail(label, f"global log missing at {OSQUERY_LOG}. "
                           "Daemon may have the wrong logger_path.")

    # Read the last 200 lines and find the most-recent unixTime.
    # Use errors='replace' - the log can contain a corrupt byte (invariant 2).
    now = int(time.time())
    staleness_limit = 90  # seconds; running_apps_snapshot runs every 5s
    most_recent = 0
    most_recent_name = ""
    es_seen = False
    try:
        # Efficiently read the tail without loading 8+ GB into memory.
        result = subprocess.run(
            ["tail", "-n", "200", str(OSQUERY_LOG)],
            capture_output=True, errors="replace", text=True
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ut = int(rec.get("unixTime", 0))
                name = rec.get("name", "")
                if ut > most_recent:
                    most_recent = ut
                    most_recent_name = name
                if name in ("process_events_stream", "file_events_stream"):
                    es_seen = True
            except Exception:
                pass
    except Exception as e:
        return fail(label, f"could not read log tail: {e}")

    age = now - most_recent
    if most_recent == 0:
        return fail(label, "log exists but contains no parseable JSON entries. "
                           "Restart: sudo launchctl kickstart -k system/io.osquery.agent")
    if age > staleness_limit:
        return fail(label, f"last entry is {age}s old (limit {staleness_limit}s). "
                           "Daemon may be stuck. "
                           "Restart: sudo launchctl kickstart -k system/io.osquery.agent")

    es_note = " (ES streams present)" if es_seen else \
              f" {YELLOW}[WARN: no ES events in last 200 lines — reboot recommended]{RESET}"
    return ok(label, f"running, last event {age}s ago ({most_recent_name}){es_note}")


def check_obs():
    """OBS must be reachable AND not already recording."""
    label = "OBS"
    creds = load_creds()
    if not creds:
        return fail(label, f"credentials file missing at {CREDS}")
    try:
        import obsws_python as obs
    except ImportError:
        return fail(label, "obsws-python not installed. Fix: "
                           "pip install obsws-python")
    try:
        cl = obs.ReqClient(host=creds.get("OBS_HOST", "localhost"),
                           port=int(creds.get("OBS_PORT", "4455")),
                           password=creds.get("OBS_PASSWORD", ""), timeout=4)
        ver = cl.get_version()
        rec = cl.get_record_status().output_active
    except Exception as e:
        return fail(label, f"not reachable: {e}. Fix: start OBS, then "
                           "Tools > WebSocket Server Settings > enable (port 4455).")
    if rec:
        return fail(label, "OBS is ALREADY RECORDING (stale session). Fix: stop it "
                           "in OBS, or the launcher's OBS quick-fix will stop it.")
    return ok(label, f"reachable (OBS {ver.obs_version}), not recording")


def check_polar(duration=8):
    """Polar must connect and emit HR samples. Strap must be worn for this."""
    label = "Polar"
    script = THESIS_DIR / "polar_listener.py"
    if not script.exists():
        return fail(label, "polar_listener.py missing")
    out = THESIS_DIR / ".preflight_polar.jsonl"
    if out.exists():
        out.unlink()
    print(f"  {YELLOW}....{RESET} Polar: listening {duration}s (strap must be worn)...")
    try:
        subprocess.run([sys.executable, "-u", str(script),
                        "--duration", str(duration), "--output", str(out)],
                       capture_output=True, text=True, timeout=duration + 20)
    except subprocess.TimeoutExpired:
        return fail(label, "listener timed out")
    if not out.exists():
        return fail(label, "no output file produced")
    hrs = []
    for line in out.read_text().splitlines():
        try:
            r = json.loads(line)
            hr = r.get("heart_rate_bpm")
            if hr:
                hrs.append(hr)
        except Exception:
            pass
    out.unlink()
    if not hrs:
        return fail(label, "connected but no HR samples. Fix: confirm strap is "
                           "worn, electrodes well moistened, strap snug; re-pair "
                           "in System Settings > Bluetooth if needed.")
    mean_hr = sum(hrs) / len(hrs)
    if mean_hr < 40 or mean_hr > 180:
        return warn(label, f"{len(hrs)} samples but HR mean {mean_hr:.0f} looks off "
                           "(check strap contact)")
    return ok(label, f"{len(hrs)} HR samples, mean {mean_hr:.0f} bpm")


def check_input(duration=4):
    """Input listener must run without the 'not trusted' Accessibility error.
    We judge by a clean exit, not by captured events: in a short idle window
    there may be no keystrokes or mouse moves to log, which is not a failure."""
    label = "Input"
    script = THESIS_DIR / "input_dynamics.py"
    if not script.exists():
        return fail(label, "input_dynamics.py missing")
    out = THESIS_DIR / ".preflight_input.jsonl"
    if out.exists():
        out.unlink()
    try:
        r = subprocess.run([sys.executable, "-u", str(script),
                            "--duration", str(duration), "--output", str(out),
                            "--track-mouse-movement"],
                           capture_output=True, text=True, timeout=duration + 15)
    except subprocess.TimeoutExpired:
        return fail(label, "listener timed out")
    combined = (r.stdout or "") + (r.stderr or "")
    low = combined.lower()
    n_events = 0
    if out.exists():
        n_events = sum(1 for _ in out.open())
        out.unlink()
    if "not trusted" in low or "not authorized" in low:
        return fail(label, "Terminal lacks Accessibility permission. Fix: System "
                           "Settings > Privacy & Security > Accessibility > add "
                           "Terminal, then reboot.")
    if r.returncode != 0 or "traceback" in low:
        tail = combined.strip().splitlines()[-1] if combined.strip() else "no output"
        return fail(label, f"listener crashed ({tail[:80]}).")
    if n_events > 0:
        return ok(label, f"runs, Accessibility OK ({n_events} events seen)")
    return ok(label, "runs, Accessibility OK (no input during idle check - expected)")


def check_focus(duration=4):
    label = "Focus"
    script = THESIS_DIR / "nsworkspace_monitor.py"
    if not script.exists():
        return fail(label, "nsworkspace_monitor.py missing")
    out = THESIS_DIR / ".preflight_focus.jsonl"
    if out.exists():
        out.unlink()
    try:
        r = subprocess.run([sys.executable, "-u", str(script),
                            "--duration", str(duration), "--output", str(out)],
                           capture_output=True, text=True, timeout=duration + 15)
    except subprocess.TimeoutExpired as e:
        # Kill the hung subprocess so it doesn't linger.
        try:
            e.process.kill() if hasattr(e, "process") else None
        except Exception:
            pass
        return fail(label, f"monitor timed out after {duration + 15}s. "
                           "Usually means the PyObjC event loop could not stop "
                           "(check CFRunLoopStop import). Also confirm Terminal has "
                           "Accessibility permission in System Settings, then reboot.")
    combined = (r.stdout or "") + (r.stderr or "")
    low = combined.lower()
    n_events = 0
    if out.exists():
        n_events = sum(1 for _ in out.open())
        out.unlink()

    # The monitor only writes a file when it actually captures a focus change.
    # During this check the operator is idle at the launcher, so capturing zero
    # changes (and writing no file) is EXPECTED and NOT a failure. We only fail
    # on a genuine problem: the process crashed (non-zero exit / traceback) or
    # macOS explicitly denied access.
    if "not trusted" in low or "not authorized" in low:
        return fail(label, "permission denied. Fix: System Settings > Privacy & "
                           "Security > Accessibility (and Automation) > enable "
                           "Terminal, then reboot.")
    if r.returncode != 0 or "traceback" in low:
        tail = combined.strip().splitlines()[-1] if combined.strip() else "no output"
        return fail(label, f"monitor crashed ({tail[:80]}). Fix: check "
                           "nsworkspace_monitor.py; confirm Accessibility, reboot.")
    if n_events > 0:
        return ok(label, f"runs, captured {n_events} focus change(s)")
    return ok(label, "runs cleanly (no app switches during idle check - expected)")


def check_disk():
    label = "Disk"
    st = os.statvfs("/")
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    if free_gb < 2:
        return fail(label, f"only {free_gb:.1f} GB free (each session ~0.3 GB)")
    return ok(label, f"{free_gb:.1f} GB free")


def main():
    skip_polar = "--skip-polar" in sys.argv
    only_polar = "--only-polar" in sys.argv

    print()
    print(f"{BOLD}============================================================{RESET}")
    print(f"{BOLD}  PRE-FLIGHT STREAM HEALTH CHECK{RESET}")
    print(f"{BOLD}============================================================{RESET}")
    if only_polar:
        print("  Polar-only check. Strap must be worn.")
    elif skip_polar:
        print("  Skipping Polar (run --only-polar once the strap is worn).")
    else:
        print("  Run with the strap worn, OBS open, participant not yet seated.")
    print()

    results = []
    if only_polar:
        results.append(check_polar())
    else:
        results.append(check_disk())
        results.append(check_osquery())
        results.append(check_obs())
        results.append(check_focus())
        results.append(check_input())
        if not skip_polar:
            results.append(check_polar())  # last, since it needs the strap worn

    print()
    print(f"{BOLD}------------------------------------------------------------{RESET}")
    if all(results):
        print(f"  {GREEN}{BOLD}ALL CHECKS HEALTHY - cleared to proceed.{RESET}")
        print(f"{BOLD}------------------------------------------------------------{RESET}")
        print()
        return 0
    else:
        n_fail = sum(1 for r in results if not r)
        print(f"  {RED}{BOLD}{n_fail} CHECK(S) FAILED - do NOT run a participant yet.{RESET}")
        print(f"  Fix the FAIL items above, then re-run this check.")
        print(f"{BOLD}------------------------------------------------------------{RESET}")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
