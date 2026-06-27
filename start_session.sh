#!/bin/bash
# start_session.sh - Phase 1 session runner (bash 3.2 compatible)
# Captures 5 synchronized streams: Polar HRV, input dynamics (incl. mouse movement),
# NSWorkspace focused-app, osquery UEBA, OBS screen+webcam recording.
# Foreground ASCII countdown timer keeps the operator oriented.
#
# Usage:
#   Direct invocation (standalone, picks own session dir):
#       bash start_session.sh                    # 5-min default
#       bash start_session.sh 1800               # 30-min
#       bash start_session.sh 2700 P03           # 45-min participant P03
#
#   From session_controller.py (recommended): controller exports SESSION_DIR
#   with a pre-created directory, this script honors it and writes streams there.

set -e

DURATION="${1:-300}"
PARTICIPANT_ID="${2:-self}"

# THESIS_DIR is the fixed shared project root. Do not derive from $HOME -
# different operator users (xulex, LABamico) both reach it via this path.
THESIS_DIR="${THESIS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
VENV_PYTHON="$THESIS_DIR/.venv/bin/python"
OSQUERY_LOG="$THESIS_DIR/osquery_logs/osqueryd.results.log"

SESSION_STAMP=$(date -u +"%Y%m%dT%H%M%SZ")

# Honor SESSION_DIR from session_controller.py if exported, otherwise
# build a default under THESIS_DIR/sessions/.
# The controller pre-creates the directory and writes session_metadata.json
# into it before spawning us - we MUST write stream files into the same place.
if [ -z "$SESSION_DIR" ]; then
    SESSION_DIR="$THESIS_DIR/sessions/${PARTICIPANT_ID}_${SESSION_STAMP}"
    mkdir -p "$SESSION_DIR"
    echo "  SESSION_DIR not set, using default: $SESSION_DIR"
else
    echo "  SESSION_DIR inherited from controller: $SESSION_DIR"
    # Sanity: the controller is supposed to create this; if it doesn't exist,
    # create it ourselves rather than fail.
    mkdir -p "$SESSION_DIR"
fi

SESSION_START_UNIX=$(date -u +"%s")
SESSION_START_ISO=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

echo "============================================================"
echo "Phase 1 session starting"
echo "============================================================"
echo "  Participant:  $PARTICIPANT_ID"
echo "  Duration:     ${DURATION}s ($(($DURATION / 60))m $(($DURATION % 60))s)"
echo "  Session ID:   ${PARTICIPANT_ID}_${SESSION_STAMP}"
echo "  Start (UTC):  $SESSION_START_ISO"
echo "  Output dir:   $SESSION_DIR"
echo ""

echo "Pre-flight checks..."
[ -f "$VENV_PYTHON" ] || { echo "  ERROR: venv Python not found at $VENV_PYTHON"; exit 1; }
[ -f "$THESIS_DIR/polar_listener.py" ] || { echo "  ERROR: polar_listener.py missing"; exit 1; }
[ -f "$THESIS_DIR/input_dynamics.py" ] || { echo "  ERROR: input_dynamics.py missing"; exit 1; }
[ -f "$THESIS_DIR/nsworkspace_monitor.py" ] || { echo "  ERROR: nsworkspace_monitor.py missing"; exit 1; }
[ -f "$THESIS_DIR/obs_recorder.py" ] || { echo "  ERROR: obs_recorder.py missing"; exit 1; }
[ -f "$THESIS_DIR/session_timer.py" ] || { echo "  ERROR: session_timer.py missing"; exit 1; }
[ -f "$THESIS_DIR/.obs_credentials" ] || { echo "  ERROR: .obs_credentials missing"; exit 1; }
[ -f "/var/osquery/osquery.conf" ] || { echo "  ERROR: /var/osquery/osquery.conf not installed"; exit 1; }
echo "  All scripts, config, and credentials present."

echo "Confirming OBS is reachable..."
"$VENV_PYTHON" -c "
import sys, os
try:
    import obsws_python as obs
    creds = {}
    with open('$THESIS_DIR/.obs_credentials') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.strip().split('=', 1)
                creds[k] = v
    cl = obs.ReqClient(host=creds.get('OBS_HOST', 'localhost'),
                       port=int(creds.get('OBS_PORT', 4455)),
                       password=creds.get('OBS_PASSWORD', ''),
                       timeout=3)
    info = cl.get_version()
    print(f'  OBS {info.obs_version} connected.')
except Exception as e:
    print(f'  ERROR: OBS not reachable: {e}')
    sys.exit(1)
" || exit 1
echo ""

POLAR_PID=""
INPUT_PID=""
FOCUS_PID=""
OBS_PID=""

cleanup() {
    # Restore cursor and clear (timer hides it)
    printf '\033[?25h\n\n'

    echo "Cleanup..."

    # Lightweight streams (Polar, input, focus) flush small JSONL files and
    # can stop quickly. Send SIGTERM, give them a few seconds, then SIGKILL.
    [ -n "$POLAR_PID" ] && kill -TERM "$POLAR_PID" 2>/dev/null || true
    [ -n "$INPUT_PID" ] && kill -TERM "$INPUT_PID" 2>/dev/null || true
    [ -n "$FOCUS_PID" ] && kill -TERM "$FOCUS_PID" 2>/dev/null || true

    # The OBS recorder needs much longer: on SIGTERM it must tell OBS to stop
    # recording, wait for OBS to finalize a multi-hundred-MB mp4 to disk, then
    # move that file into the session dir. SIGKILLing it early (as a flat
    # 3-second sleep did) orphans the recording in OBS's own folder. Send it
    # SIGTERM and WAIT for it to exit on its own, up to OBS_STOP_GRACE seconds.
    [ -n "$OBS_PID" ] && kill -TERM "$OBS_PID" 2>/dev/null || true

    sleep 3
    [ -n "$POLAR_PID" ] && kill -KILL "$POLAR_PID" 2>/dev/null || true
    [ -n "$INPUT_PID" ] && kill -KILL "$INPUT_PID" 2>/dev/null || true
    [ -n "$FOCUS_PID" ] && kill -KILL "$FOCUS_PID" 2>/dev/null || true

    # Wait for the OBS recorder to finish its own stop-and-move. Poll its PID.
    if [ -n "$OBS_PID" ]; then
        echo "  Waiting for OBS recorder to finalize and move the recording..."
        OBS_STOP_GRACE=30
        waited=0
        while kill -0 "$OBS_PID" 2>/dev/null; do
            sleep 1
            waited=$((waited + 1))
            if [ "$waited" -ge "$OBS_STOP_GRACE" ]; then
                echo "  WARNING: OBS recorder still alive after ${OBS_STOP_GRACE}s; killing."
                echo "           Recording may be orphaned in the recordings folder;"
                echo "           recover with reslice_osquery.py."
                kill -KILL "$OBS_PID" 2>/dev/null || true
                break
            fi
        done
        echo "  OBS recorder exited after ${waited}s."
    fi

    if [ -f "$OSQUERY_LOG" ]; then
        SESSION_END_UNIX=$(date -u +"%s")
        OSQUERY_SLICE="$SESSION_DIR/osquery.jsonl"
        echo "  Slicing osquery events to session window..."
        "$VENV_PYTHON" -u -c "
import json
start = $SESSION_START_UNIX
end = $SESSION_END_UNIX
kept = 0
try:
    fin = open('$OSQUERY_LOG', encoding='utf-8', errors='replace')
    fout = open('$OSQUERY_SLICE', 'w')
    for line in fin:
        line = line.strip()
        if not line: continue
        try:
            r = json.loads(line)
            ut = int(r.get('unixTime', 0))
            if start <= ut <= end:
                fout.write(line + '\n')
                kept += 1
        except: pass
    fin.close()
    fout.close()
    print(f'  Captured {kept} osquery events for this session.')
except Exception as e:
    print(f'  WARNING: osquery slice failed: {e}')
"
    else
        echo "  WARNING: osquery global log not found at $OSQUERY_LOG"
    fi

    echo ""
    echo "Session output:"
    ls -la "$SESSION_DIR"
    echo ""
    echo "Total elapsed: $(($(date -u +%s) - $SESSION_START_UNIX))s"
}
trap cleanup EXIT INT TERM

# osquery runs continuously as a system daemon via launchd (loaded at boot).
# We do NOT reset it here - doing so would require sudo, which triggers a
# password prompt that cannot appear during a participant session.
# Instead we passively confirm the daemon is alive and writing. The events
# it captures during this session are sliced from its global log in cleanup().
echo "Checking osquery daemon (passive, no reset)..."
if pgrep -x osqueryd > /dev/null; then
    echo "  osqueryd is running."
    if [ -f "$OSQUERY_LOG" ]; then
        echo "  Global log present: $OSQUERY_LOG"
    else
        echo "  WARNING: global log not found at $OSQUERY_LOG (slice will be empty)."
    fi
else
    echo "  WARNING: osqueryd not running. osquery.jsonl will be empty for this session."
    echo "  To start it (operator, before session): sudo launchctl kickstart -k system/io.osquery.agent"
fi
echo ""

echo "Starting Polar listener..."
"$VENV_PYTHON" -u "$THESIS_DIR/polar_listener.py" --duration "$DURATION" --output "$SESSION_DIR/polar.jsonl" > "$SESSION_DIR/polar.log" 2>&1 &
POLAR_PID=$!
echo "  PID $POLAR_PID"

echo "Starting input dynamics (with mouse movement tracking)..."
"$VENV_PYTHON" -u "$THESIS_DIR/input_dynamics.py" --duration "$DURATION" --output "$SESSION_DIR/input.jsonl" --track-mouse-movement > "$SESSION_DIR/input.log" 2>&1 &
INPUT_PID=$!
echo "  PID $INPUT_PID"

echo "Starting NSWorkspace monitor..."
"$VENV_PYTHON" -u "$THESIS_DIR/nsworkspace_monitor.py" --duration "$DURATION" --output "$SESSION_DIR/focused_app.jsonl" > "$SESSION_DIR/focused_app.log" 2>&1 &
FOCUS_PID=$!
echo "  PID $FOCUS_PID"

echo "Starting OBS recorder..."
"$VENV_PYTHON" -u "$THESIS_DIR/obs_recorder.py" --duration "$DURATION" --output "$SESSION_DIR/recording.mp4" > "$SESSION_DIR/recording.log" 2>&1 &
OBS_PID=$!
echo "  PID $OBS_PID"
echo ""

# Brief moment for streams to finish initializing before the timer takes over the screen
sleep 2

# Foreground ASCII timer (single Python process - no busy spawning, no osquery noise)
"$VENV_PYTHON" -u "$THESIS_DIR/session_timer.py" \
    --duration "$DURATION" \
    --participant "$PARTICIPANT_ID" \
    --session-id "${PARTICIPANT_ID}_${SESSION_STAMP}" || true

# After timer exits, allow streams to flush
sleep 3
echo "Session complete."
