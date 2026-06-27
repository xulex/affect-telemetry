#!/usr/bin/env bash
# Lightweight video/OCR stack for AI-usage detection on Azure CPU VMs.
# No GPU / py-feat required. Idempotent.
set -euo pipefail

VENV=/opt/videobatch/venv

ensure_python() {
  if command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
    return
  fi
  echo "python3.11 not found — installing (deadsnakes PPA)..."
  sudo apt-get update -qq
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -qq
  sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
  PY=python3.11
}

ensure_python

echo "Installing system packages (ffmpeg, tesseract)..."
sudo apt-get update -qq
sudo apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-eng libtesseract-dev

sudo mkdir -p /opt/videobatch
sudo chown "$USER":"$USER" /opt/videobatch

if [[ ! -f "$VENV/bin/activate" ]]; then
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install -q --upgrade pip wheel
pip install -q pillow pytesseract

echo "OK video venv at $VENV"
ffmpeg -version | head -1
tesseract --version | head -1
python -c "import pytesseract; from PIL import Image; print('python deps OK')"
