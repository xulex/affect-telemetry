#!/usr/bin/env bash
# Run AI video OCR for ONE session on this VM. Invoked by tmux or manually.
#
#   SESSION_ID=P11_20260606T140334Z bash ~/thesis-phase1/azure/parallel/vm_video_run.sh
set -euo pipefail

: "${SESSION_ID:?Set SESSION_ID}"

REPO="${REPO:-$HOME/thesis-phase1}"
SESSION_DIR="$REPO/sessions/$SESSION_ID"
LOG="$SESSION_DIR/video_run.log"
VENV=/opt/videobatch/venv/bin/python

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

echo "========== $(date -u) VM $(hostname) VIDEO START $SESSION_ID =========="
df -h / | tail -1

if [[ ! -f "$SESSION_DIR/recording.mp4" ]]; then
  echo "ERROR: missing $SESSION_DIR/recording.mp4"
  exit 1
fi

if [[ ! -x "$VENV" ]]; then
  echo "Installing video/OCR venv..."
  bash "$REPO/azure/install_video_ubuntu.sh"
  VENV=/opt/videobatch/venv/bin/python
fi

# Ensure layer-1 report exists on VM
if [[ ! -f "$SESSION_DIR/ai_usage_report.json" ]]; then
  echo "Generating ai_usage_report.json (layer 1)..."
  python3 "$REPO/analysis/detect_ai_usage.py" "$SESSION_DIR" --write-json
fi

"$VENV" "$REPO/analysis/process_ai_video.py" "$SESSION_DIR" \
  --sample-every "${SAMPLE_EVERY:-25}" \
  --write-json

echo "========== $(date -u) VM $(hostname) VIDEO DONE  $SESSION_ID =========="
