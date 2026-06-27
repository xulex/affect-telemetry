#!/usr/bin/env bash
# Pull ai_video_report.json (and log) from each video VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=video_mac_common.sh
source "$SCRIPT_DIR/video_mac_common.sh"

STAGING="${STAGING:-$HOME/Downloads/thesis-video-download}"
mkdir -p "$STAGING"

_ASSIGN_LINES=()
while IFS= read -r _aline; do
  _ASSIGN_LINES+=("$_aline")
done < <(read_assignments)
for line in "${_ASSIGN_LINES[@]}"; do
  [[ -z "$line" ]] && continue
  read -r VM_IP SESSION_ID _LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  DEST_DIR="$THESIS/sessions/$SESSION_ID"
  STAGE_DIR="$STAGING/$SESSION_ID"
  mkdir -p "$STAGE_DIR" "$(dirname "$DEST_DIR")" 2>/dev/null || true

  echo "=== Download $SESSION_ID from $VM_IP ==="
  scp -i "$KEY" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/ai_video_report.json" \
    "$STAGE_DIR/" 2>/dev/null || echo "  WARN: ai_video_report.json missing"
  scp -i "$KEY" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/video_run.log" \
    "$STAGE_DIR/" 2>/dev/null || true

  for f in ai_video_report.json video_run.log; do
    if [[ -f "$STAGE_DIR/$f" ]]; then
      cp "$STAGE_DIR/$f" "$DEST_DIR/" 2>/dev/null || echo "  kept: $STAGE_DIR/$f"
    fi
  done
  echo ""
done

THESIS="$THESIS" STAGING="$STAGING" python3 <<'PY'
import json
from pathlib import Path
import os

th = Path(os.environ.get("THESIS", os.getcwd()))
staging = Path(os.environ.get("STAGING", Path.home() / "Downloads/thesis-video-download"))

for p in sorted(staging.glob("*/ai_video_report.json")):
    r = json.loads(p.read_text())
    print(p.parent.name,
          "web_ai", r.get("used_ai_web"),
          "merged", r.get("merged_used_ai"),
          r.get("merged_confidence"),
          r.get("ai_domains_seen"))
PY

echo "Done. Staging: $STAGING"
