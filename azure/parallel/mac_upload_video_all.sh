#!/usr/bin/env bash
# Upload repo + one session (recording + reports) per VM for video OCR batch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=video_mac_common.sh
source "$SCRIPT_DIR/video_mac_common.sh"

echo "THESIS=$THESIS"
echo "KEY=$KEY"
echo "Video assignments: $ASSIGNMENTS"
N="$(assignment_count)"
echo "VMs in queue: $N"
echo ""

IDX=0
_ASSIGN_LINES=()
while IFS= read -r _aline; do
  _ASSIGN_LINES+=("$_aline")
done < <(read_assignments)
for line in "${_ASSIGN_LINES[@]}"; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  read -r VM_IP SESSION_ID _LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  IDX=$((IDX + 1))
  SESSION_DIR="$THESIS/sessions/$SESSION_ID"

  for f in recording.mp4; do
    if [[ ! -f "$SESSION_DIR/$f" ]]; then
      echo "ERROR: missing $SESSION_DIR/$f"
      exit 1
    fi
  done

  if [[ ! -f "$SESSION_DIR/ai_usage_report.json" ]]; then
    echo "Generating ai_usage_report.json for $SESSION_ID..."
    python3 "$THESIS/analysis/detect_ai_usage.py" "$SESSION_DIR" --write-json
  fi

  echo "=== [$IDX/$N] $VM_IP → $SESSION_ID (~$(du -h "$SESSION_DIR/recording.mp4" | cut -f1)) ==="

  rsync -avz --progress -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.venv' \
    --exclude 'sessions' \
    --exclude 'osquery_logs' \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude '.obs_credentials' \
    --exclude '*.pem' \
    --exclude 'azure/parallel/vm_assignments.env' \
    --exclude 'azure/parallel/vm_video_assignments.env' \
    "$THESIS/" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/"

  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new \
    "${VM_USER}@${VM_IP}" "mkdir -p ~/thesis-phase1/sessions/${SESSION_ID}"

  rsync -avz --progress -e "ssh -i $KEY" \
    "$SESSION_DIR/recording.mp4" \
    "$SESSION_DIR/ai_usage_report.json" \
    "$SESSION_DIR/recording_start.txt" \
    "$SESSION_DIR/session_metadata.json" \
    "$SESSION_DIR/focused_app.jsonl" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/" 2>/dev/null || \
  rsync -avz --progress -e "ssh -i $KEY" \
    "$SESSION_DIR/recording.mp4" \
    "$SESSION_DIR/ai_usage_report.json" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/"

  echo "  OK $VM_IP"
  echo ""
done

echo "All video uploads done ($IDX VMs)."
