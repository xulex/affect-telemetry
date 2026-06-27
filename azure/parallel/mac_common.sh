#!/usr/bin/env bash
# Shared paths for Mac-side parallel batch scripts.
set -euo pipefail

THESIS="${THESIS:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)}"
KEY="${KEY:-$HOME/.ssh/xulex-keyAzure.pem}"
VM_USER="${VM_USER:-xulex}"
ASSIGNMENTS="${ASSIGNMENTS:-$THESIS/azure/parallel/vm_assignments.env}"

if [[ ! -f "$KEY" ]]; then
  echo "ERROR: SSH key not found: $KEY"
  echo "  cp $THESIS/azure/xulex-keyAzure.pem ~/.ssh/ && chmod 400 ~/.ssh/xulex-keyAzure.pem"
  exit 1
fi

if [[ ! -f "$ASSIGNMENTS" ]]; then
  echo "ERROR: missing $ASSIGNMENTS"
  echo "  cp $THESIS/azure/parallel/vm_assignments.env.example $ASSIGNMENTS"
  echo "  Edit with one VM_IP + SESSION_ID per line (4 VMs for 4 sessions)."
  exit 1
fi

read_assignments() {
  grep -v '^#' "$ASSIGNMENTS" | grep -v '^[[:space:]]*$' | tr -d '\r'
}

assignment_count() {
  read_assignments | wc -l | tr -d ' '
}
