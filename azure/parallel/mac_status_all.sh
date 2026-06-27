#!/usr/bin/env bash
# Status for all GPU VMs: tmux, % complete, last log line.
# Uses the ACTIVE session on each VM when tmux is running (not just vm_assignments.env).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac_common.sh
source "$SCRIPT_DIR/mac_common.sh"

while read -r VM_IP ASSIGNED_SID LABEL; do
  [[ -z "${VM_IP:-}" || -z "${ASSIGNED_SID:-}" ]] && continue
  echo "=== $LABEL $VM_IP ==="
  ssh -i "$KEY" -o ConnectTimeout=8 "${VM_USER}@${VM_IP}" bash -s <<EOF || echo "  (ssh failed)"
export ASSIGNED_SID='$ASSIGNED_SID'

python3 <<'PY'
import os, re, subprocess
from pathlib import Path

assigned = os.environ["ASSIGNED_SID"]
home = Path.home()
sessions_root = home / "thesis-phase1/sessions"


def detect_active_session() -> str | None:
    # 1) SESSION_ID from running vm_run / au_benchmark process
    try:
        out = subprocess.check_output(
            ["ps", "aux"], text=True, errors="replace"
        )
        for line in out.splitlines():
            if "vm_run.sh" in line or "au_benchmark_colab.py" in line:
                m = re.search(r"SESSION_ID=([^\s'\"]+|'[^']+'|\"[^\"]+\")", line)
                if m:
                    sid = m.group(1).strip("'\"")
                    if (sessions_root / sid).is_dir():
                        return sid
    except Exception:
        pass

    # 2) Most recently updated au_run.log
    logs = sorted(
        sessions_root.glob("*/au_run.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for log in logs[:5]:
        sid = log.parent.name
        # skip if log empty and no work dir
        work = Path(f"/tmp/colab_work/{sid}/chunks")
        if log.stat().st_size > 0 or work.exists():
            return sid

    # 3) Most recently modified colab_work dir
    work_root = Path("/tmp/colab_work")
    if work_root.is_dir():
        dirs = sorted(
            [d for d in work_root.iterdir() if d.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if dirs:
            return dirs[0].name
    return None


tmux = os.system("tmux has-session -t au 2>/dev/null") == 0
print(f"  tmux: {'RUNNING' if tmux else 'stopped'}")

active = detect_active_session() if tmux else None
sid = active or assigned

if active and active != assigned:
    print(f"  active:   {active}  (queued in assignments: {assigned})")
elif active:
    print(f"  session:  {active}")
else:
    print(f"  session:  {assigned}  (idle / use assignments)")

session = sessions_root / sid
work = Path(f"/tmp/colab_work/{sid}/chunks")
out_csv = session / "facial_au.csv"
log_path = session / "au_run.log"

if out_csv.is_file():
    rows = "(unknown rows)"
    try:
        import pandas as pd
        df = pd.read_csv(out_csv, usecols=["timestamp_utc"])
        rows = f"{len(df)} rows"
    except Exception:
        pass
    print(f"  progress: 100% DONE  ({rows})")
    print(f"  output:   {out_csv} ({out_csv.stat().st_size / 1e6:.1f} MB)")
    raise SystemExit(0)

log = log_path.read_text(errors="replace") if log_path.is_file() else ""
total_chunks = 6
m = re.search(r"Chunked run:\s*(\d+)\s*x\s*\d+s", log)
if m:
    total_chunks = max(1, int(m.group(1)))

saved = sorted(work.glob("chunk_*.csv"))
n_saved = len(saved)

active_idx = None
for hit in re.findall(r"(?:detect|ffmpeg)\s+chunk_(\d+)", log):
    active_idx = int(hit)

frame_cur, frame_tot = 0, 0
frame_hits = re.findall(r"(\d+)/(\d+)\s+\[", log)
if frame_hits:
    frame_cur, frame_tot = map(int, frame_hits[-1])

in_chunk_frac = (frame_cur / frame_tot) if frame_tot else 0.0

if active_idx is None:
    active_idx = n_saved
elif (work / f"chunk_{active_idx:03d}.csv").is_file():
    active_idx = max(active_idx + 1, n_saved)

if n_saved > active_idx:
    units_done = n_saved + in_chunk_frac
elif active_idx is not None and not (work / f"chunk_{active_idx:03d}.csv").is_file():
    units_done = active_idx + in_chunk_frac
else:
    units_done = max(n_saved, active_idx) + (0.0 if n_saved >= total_chunks else in_chunk_frac)

pct = min(99.9, 100.0 * units_done / total_chunks) if total_chunks else 0.0

chunk_note = f"chunk {active_idx:03d}" if active_idx is not None else "chunk ?"
if frame_tot:
    chunk_note += f"  frames {frame_cur}/{frame_tot}"
print(f"  progress: {pct:5.1f}%  ({n_saved}/{total_chunks} chunks saved, {chunk_note})")

eta_m = re.search(r"<\s*(\d+):(\d{2}):\d{2}", log)
if eta_m and tmux and pct < 100:
    h, mm = int(eta_m.group(1)), int(eta_m.group(2))
    print(f"  eta:      ~{h}h {mm}m remaining this chunk (from last log line)")

if log_path.is_file():
    lines = [ln for ln in log.splitlines() if ln.strip()]
    for ln in reversed(lines[-30:]):
        if any(k in ln for k in ("Session chunks", "detect chunk", "ffmpeg chunk", "Checkpoint", "Wrote")):
            print(f"  last:     {ln.strip()[:100]}")
            break
    else:
        print(f"  last:     {lines[-1].strip()[:100] if lines else '(empty log)'}")
else:
    print("  last:     no log yet")
PY
EOF
  echo ""
done < <(read_assignments)
