#!/usr/bin/env bash
# Pull facial_au.csv from each VM back to Mac session folders.
# Falls back to ~/Downloads/thesis-au-download/ if /Users/Shared is not writable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac_common.sh
source "$SCRIPT_DIR/mac_common.sh"

STAGING="${STAGING:-$HOME/Downloads/thesis-au-download}"
mkdir -p "$STAGING"

while read -r VM_IP SESSION_ID _LABEL; do
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  DEST="$THESIS/sessions/$SESSION_ID/facial_au.csv"
  STAGE="$STAGING/$SESSION_ID/facial_au.csv"
  mkdir -p "$(dirname "$STAGE")"

  echo "=== Download $SESSION_ID from $VM_IP ==="
  scp -i "$KEY" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/facial_au.csv" \
    "$STAGE"
  ls -lh "$STAGE"

  mkdir -p "$(dirname "$DEST")" 2>/dev/null || true
  if cp "$STAGE" "$DEST" 2>/dev/null; then
    echo "  -> $DEST"
  else
    echo "  WARN: cannot write $DEST (Permission denied)"
    echo "  kept: $STAGE"
    echo "  move manually in Finder, or: sudo chown -R \$(whoami) $THESIS/sessions"
  fi
  echo ""
done < <(read_assignments)

echo "Running quick QA..."
THESIS="$THESIS" STAGING="$STAGING" python3 <<'PY'
import pandas as pd
from pathlib import Path
import os

th = Path(os.environ["THESIS"])
staging = Path(os.environ["STAGING"])
assign = th / "azure/parallel/vm_assignments.env"

for line in assign.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split()
    if len(parts) < 2:
        continue
    sid = parts[1]
    p = th / "sessions" / sid / "facial_au.csv"
    if not p.exists():
        p = staging / sid / "facial_au.csv"
    if not p.exists():
        print(sid, "MISSING")
        continue
    df = pd.read_csv(p)
    print(sid, "rows", len(df), "cols", len(df.columns),
          "ts_ok", df["timestamp_utc"].notna().all(), "path", p)
PY

echo "Done. Staging dir: $STAGING"
