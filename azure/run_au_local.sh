#!/usr/bin/env bash
# Run chunked AU extraction on the VM — always uses /opt/aubatch/venv.
#
# Usage (inside tmux):
#   bash ~/thesis-phase1/azure/run_au_local.sh \
#     --session-dir ~/thesis-phase1/sessions/NA_self_20260516_20260516T103935Z \
#     --input recording.mp4 --fps 4 --batch-size 4 --progress \
#     --output facial_au_azure.csv \
#     --work-dir /tmp/colab_work/NA_self_20260516_20260516T103935Z
set -euo pipefail

VENV=/opt/aubatch/venv
REPO="${REPO:-$HOME/thesis-phase1}"

if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "ERROR: missing $VENV — run: bash $REPO/azure/install_feat_ubuntu.sh"
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "Python: $(which python)"
echo "Venv:   $VIRTUAL_ENV"
python -c "import sys; assert sys.prefix == '$VENV', sys.prefix"
nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo "WARN: nvidia-smi failed"

exec python "$REPO/colab/au_benchmark_colab.py" "$@"
