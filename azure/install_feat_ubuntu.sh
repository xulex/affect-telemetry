#!/usr/bin/env bash
# Pinned py-feat stack for Ubuntu 22.04 GPU VM (Azure NC T4).
# Matches thesis Colab 2b / Windows aubatch venv intent.
set -euo pipefail

VENV=/opt/aubatch/venv

ensure_python() {
  if command -v python3.11 >/dev/null 2>&1; then
    echo "Using python3.11"
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

echo "Installing ffmpeg (required for video chunking)..."
sudo apt-get update -qq
sudo apt-get install -y ffmpeg
command -v ffmpeg

sudo mkdir -p /opt/aubatch
sudo chown "$USER":"$USER" /opt/aubatch

"$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

pip install -q --upgrade pip wheel

# numpy 1.26 — py-feat 0.6.1 uses np.mat (removed in numpy 2.x)
pip uninstall -y numexpr opencv-python-headless opencv-python 2>/dev/null || true
pip install -q --force-reinstall --only-binary=numpy,scipy "numpy==1.26.4" "scipy==1.13.1"
pip install -q --force-reinstall --no-deps "numexpr==2.8.4"
pip install -q --force-reinstall --no-deps "opencv-python-headless==4.8.1.78"

# PyTorch with CUDA — adjust cu121 if driver is older; T4 on Ubuntu 22.04 is usually fine
pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cu121

pip install -q --no-deps "py-feat==0.6.1" "nltools==0.5.1"
pip install -q --no-deps "tables==3.9.2" pynv

# WITH dependencies (do not use --no-deps here — matplotlib/seaborn need pyparsing etc.)
pip install -q \
  "pywavelets>=0.3.0" "h5py>=2.7.0" "Pillow>=6.0.0" \
  "scikit-learn>=1.2" "scikit-image>=0.19" "joblib" "threadpoolctl" \
  "matplotlib>=3.7,<3.11" "seaborn>=0.12" \
  "easing-functions" "celluloid" "kornia" "kornia_rs" "av>=9.2.0" "xgboost>=1.6.0" \
  nibabel nilearn requests
pip install -q "pandas>=2.0,<2.3" tqdm

# kornia 0.8+ requires kornia_rs; install explicitly (often missing on partial/old venvs)
pip install -q kornia_rs
python -c "import kornia_rs"

python - <<'PY'
import numpy as np, scipy, numexpr, cv2, torch
import scipy.integrate as si
import kornia_rs  # noqa: F401
import feat
from feat import Detector

assert int(np.__version__.split(".")[0]) < 2
assert hasattr(si, "simps")
assert feat.__version__.startswith("0.6.1")
Detector(
    face_model="retinaface",
    landmark_model="mobilefacenet",
    au_model="xgb",
    emotion_model="resmasknet",
    facepose_model="img2pose",
)
print("OK numpy", np.__version__, "numexpr", numexpr.__version__, "cv2", cv2.__version__)
print("OK torch", torch.__version__, "cuda", torch.cuda.is_available())
if torch.cuda.is_available():
    print("OK gpu", torch.cuda.get_device_name(0))
print("OK py-feat", feat.__version__)
PY

echo ""
echo "Activate with: source $VENV/bin/activate"
