#!/usr/bin/env bash
# Upload to VMs 2–4 only (skip the first VM IP if already uploaded).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac_common.sh
source "$SCRIPT_DIR/mac_common.sh"

SKIP_IP="${SKIP_IP:-}"

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" =~ ^# ]] && continue
  read -r VM_IP SESSION_ID _LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  [[ "$VM_IP" == "$SKIP_IP" ]] && echo "SKIP $VM_IP ($SESSION_ID)" && continue

  SESSION_DIR="$THESIS/sessions/$SESSION_ID"
  echo "=== $VM_IP → $SESSION_ID ==="

  rsync -avz --progress -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
    --exclude '.venv' --exclude 'sessions' --exclude 'osquery_logs' \
    --exclude '__pycache__' --exclude '.git' \
    --exclude '.obs_credentials' --exclude '*.pem' \
    --exclude 'azure/parallel/vm_assignments.env' \
    "$THESIS/" "${VM_USER}@${VM_IP}:~/thesis-phase1/"

  ssh -i "$KEY" "${VM_USER}@${VM_IP}" "mkdir -p ~/thesis-phase1/sessions/${SESSION_ID}"

  rsync -avz --progress -e "ssh -i $KEY" \
    "$SESSION_DIR/" \
    "${VM_USER}@${VM_IP}:~/thesis-phase1/sessions/${SESSION_ID}/"

  echo "  OK $VM_IP"
done < <(read_assignments)

echo "Remaining uploads done."
