#!/usr/bin/env bash
# Download one session folder from Azure Blob, run chunked AU extraction, upload CSV.
#
# Prerequisites on VM:
#   - install_feat_ubuntu.sh done
#   - ~/thesis-phase1 repo present
#   - env: STORAGE_ACCOUNT, CONTAINER, SAS, SESSION_ID
#
# Usage:
#   export STORAGE_ACCOUNT=thesisphase1data
#   export CONTAINER=sessions
#   export SAS='?sv=...'
#   export SESSION_ID=NA_self_20260516_20260516T103935Z
#   bash azure/run_one_session.sh

set -euo pipefail

: "${STORAGE_ACCOUNT:?Set STORAGE_ACCOUNT}"
: "${CONTAINER:?Set CONTAINER}"
: "${SAS:?Set SAS (include leading ?)}"
: "${SESSION_ID:?Set SESSION_ID}"

WORK="${WORK:-/data/sessions}"
REPO="${REPO:-$HOME/thesis-phase1}"
BATCH_SIZE="${BATCH_SIZE:-4}"
FPS="${FPS:-4}"
OUTPUT="${OUTPUT:-facial_au_azure.csv}"
PROGRESS="${PROGRESS:-1}"

mkdir -p "$WORK"
BASE_URL="https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}"

echo "=== Download session from blob ==="
azcopy copy \
  "${BASE_URL}/${SESSION_ID}${SAS}" \
  "${WORK}/${SESSION_ID}" \
  --recursive

VIDEO="${WORK}/${SESSION_ID}/recording.mp4"
if [[ ! -f "$VIDEO" ]]; then
  echo "ERROR: missing $VIDEO"
  exit 1
fi
ls -lh "$VIDEO"

echo "=== GPU ==="
nvidia-smi || true

echo "=== Run extraction (chunked, ~3-6h for 20min on T4) ==="
# shellcheck disable=SC1091
source /opt/aubatch/venv/bin/activate
cd "$REPO"

ARGS=(
  --session-dir "${WORK}/${SESSION_ID}"
  --input recording.mp4
  --fps "$FPS"
  --batch-size "$BATCH_SIZE"
  --output "$OUTPUT"
  --work-dir "/tmp/colab_work/${SESSION_ID}"
)
if [[ "$PROGRESS" == "1" ]]; then
  ARGS+=(--progress)
fi

python colab/au_benchmark_colab.py "${ARGS[@]}"

OUT="${WORK}/${SESSION_ID}/${OUTPUT}"
echo "=== Upload result ==="
azcopy copy \
  "$OUT" \
  "${BASE_URL}/${SESSION_ID}/${OUTPUT}${SAS}"

echo "Done. Local CSV: $OUT"
echo "Blob path: ${CONTAINER}/${SESSION_ID}/${OUTPUT}"
