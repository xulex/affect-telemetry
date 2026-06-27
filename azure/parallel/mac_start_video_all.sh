#!/usr/bin/env bash
# Start video OCR workers in tmux on each assigned VM.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=video_mac_common.sh
source "$SCRIPT_DIR/video_mac_common.sh"

_ASSIGN_LINES=()
while IFS= read -r _aline; do
  _ASSIGN_LINES+=("$_aline")
done < <(read_assignments)
for line in "${_ASSIGN_LINES[@]}"; do
  [[ -z "$line" ]] && continue
  read -r VM_IP SESSION_ID LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue

  echo "=== Start video OCR $SESSION_ID on $VM_IP (tmux: video) ==="

  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "${VM_USER}@${VM_IP}" bash -s <<EOF
set -euo pipefail
chmod +x ~/thesis-phase1/azure/parallel/vm_video_run.sh 2>/dev/null || true
chmod +x ~/thesis-phase1/azure/install_video_ubuntu.sh 2>/dev/null || true
tmux kill-session -t video 2>/dev/null || true
tmux new -d -s video "SESSION_ID='$SESSION_ID' bash ~/thesis-phase1/azure/parallel/vm_video_run.sh"
echo "tmux session 'video' started on \$(hostname) for $SESSION_ID"
EOF

done

echo ""
echo "Monitor: bash $SCRIPT_DIR/mac_status_video_all.sh"
