#!/usr/bin/env bash
# SSH to each VM and start AU extraction in tmux (one session per VM).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac_common.sh
source "$SCRIPT_DIR/mac_common.sh"

REMOTE_RUN="$HOME/thesis-phase1/azure/parallel/vm_run.sh"

while read -r VM_IP SESSION_ID LABEL; do
  [[ -z "${VM_IP:-}" || -z "${SESSION_ID:-}" ]] && continue
  NAME="${LABEL:-au}"

  echo "=== Start $SESSION_ID on $VM_IP (tmux: au) ==="

  ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "${VM_USER}@${VM_IP}" bash -s <<EOF
set -euo pipefail
chmod +x ~/thesis-phase1/azure/parallel/vm_run.sh 2>/dev/null || true
tmux kill-session -t au 2>/dev/null || true
tmux new -d -s au "SESSION_ID='$SESSION_ID' bash ~/thesis-phase1/azure/parallel/vm_run.sh"
echo "tmux session 'au' started on \$(hostname) for $SESSION_ID"
EOF

done < <(read_assignments)

echo ""
echo "Monitor any VM:"
echo "  ssh -i $KEY ${VM_USER}@<IP> 'tmux attach -t au'"
