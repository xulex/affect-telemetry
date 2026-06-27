#!/usr/bin/env bash
# Upload repo code + ONE session folder to each GPU VM (parallel batch).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac_common.sh
source "$SCRIPT_DIR/mac_common.sh"

echo "THESIS=$THESIS"
echo "KEY=$KEY"
echo "Assignments: $ASSIGNMENTS"
N="$(assignment_count)"
echo "VMs in queue: $N"
if [[ "$N" -lt 1 ]]; then
  echo "ERROR: no assignments in $ASSIGNMENTS"
  exit 1
fi
echo ""

IDX=0
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  read -r VM_IP SESSION_ID _LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  IDX=$((IDX + 1))
  echo "--- [$IDX/$N] ---"
  SESSION_DIR="$THESIS/sessions/$SESSION_ID"
  if [[ ! -f "$SESSION_DIR/recording.mp4" ]]; then
    echo "ERROR: missing $SESSION_DIR/recording.mp4"
    exit 1
  fi

  echo "=== $VM_IP → $SESSION_ID ==="

  rsync -avz --progress -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.venv' \
    --exclude 'sessions' \
    --exclude 'osquery_logs' \
    --exclude '__pycache__' \
    --exclude '.git' \
    --exclude '.obs_credentials' \
    --exclude '*.pem' \
    --exclude 'azure/parallel/vm_assignments.env' \
    "$THESIS/" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/"

  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new \
    "${VM_USER}@${VM_IP}" "mkdir -p ~/thesis-phase1/sessions/${SESSION_ID}"

  rsync -avz --progress -e "ssh -i $KEY" \
    "$SESSION_DIR/" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/"

  echo "  OK $VM_IP"
  echo ""
done < <(read_assignments)

if [[ "$IDX" -ne "$N" ]]; then
  echo "WARNING: processed $IDX VMs but expected $N — check $ASSIGNMENTS"
  exit 1
fi
echo "All uploads done ($IDX VMs)."
