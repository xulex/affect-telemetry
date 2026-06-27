#!/usr/bin/env bash
# Run ONE session on this GPU VM. Invoked by tmux or manually.
#
#   SESSION_ID=<SESSION_ID> bash ~/thesis-phase1/azure/parallel/vm_run.sh
set -euo pipefail

: "${SESSION_ID:?Set SESSION_ID}"

REPO="${REPO:-$HOME/thesis-phase1}"
SESSION_DIR="$REPO/sessions/$SESSION_ID"
WORK_DIR="/tmp/colab_work/$SESSION_ID"
LOG="$REPO/sessions/$SESSION_ID/au_run.log"

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1

echo "========== $(date -u) VM $(hostname) START $SESSION_ID =========="
nvidia-smi || true
df -h / | tail -1

if [[ ! -f "$SESSION_DIR/recording.mp4" ]]; then
  echo "ERROR: missing $SESSION_DIR/recording.mp4"
  exit 1
fi

if [[ ! -f /opt/aubatch/venv/bin/activate ]]; then
  echo "Installing venv..."
  export PIP_NO_CACHE_DIR=1
  bash "$REPO/azure/install_feat_ubuntu.sh"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Installing ffmpeg..."
  sudo apt-get update -qq
  sudo apt-get install -y ffmpeg
fi

bash "$REPO/azure/run_au_local.sh" \
  --session-dir "$SESSION_DIR" \
  --input recording.mp4 \
  --fps 4 \
  --batch-size 4 \
  --progress \
  --output facial_au.csv \
  --work-dir "$WORK_DIR"

echo "========== $(date -u) VM $(hostname) DONE  $SESSION_ID =========="
