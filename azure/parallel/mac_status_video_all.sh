#!/usr/bin/env bash
# Status for video OCR batch VMs.
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
  read -r VM_IP ASSIGNED_SID LABEL <<< "$line"
  [[ -z "${VM_IP:-}" || -z "${ASSIGNED_SID:-}" ]] && continue
  echo "=== ${LABEL:-video} $VM_IP ==="
  ssh -i "$KEY" -o ConnectTimeout=8 "${VM_USER}@${VM_IP}" bash -s <<EOF || echo "  (ssh failed)"
export ASSIGNED_SID='$ASSIGNED_SID'
export SESSION_DIR="\$HOME/thesis-phase1/sessions/\$ASSIGNED_SID"
LOG="\$SESSION_DIR/video_run.log"
REPORT="\$SESSION_DIR/ai_video_report.json"

if tmux has-session -t video 2>/dev/null; then
  echo "  tmux: RUNNING"
else
  echo "  tmux: stopped"
fi

if [[ -f "\$REPORT" ]]; then
  python3 - <<'PY'
import json, os
p = os.path.join(os.environ["SESSION_DIR"], "ai_video_report.json")
r = json.load(open(p))
print(f"  status:   DONE")
print(f"  used_ai_web: {r.get('used_ai_web')}")
print(f"  confidence:  {r.get('confidence')}")
print(f"  merged:      {r.get('merged_used_ai')} ({r.get('merged_confidence')})")
print(f"  domains:     {', '.join(r.get('ai_domains_seen') or []) or '(none)'}")
print(f"  frame_hits:  {r.get('web_ai_frames_hit', 0)}")
PY
else
  echo "  status:   in progress or not started"
fi

if [[ -f "\$LOG" ]]; then
  echo "  last:"
  tail -3 "\$LOG" | sed 's/^/    /'
fi
EOF
  echo ""
done
