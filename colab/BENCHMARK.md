# AU extraction pilot benchmark (self-data only)

Feasibility test: **5-minute clip** from your own `NA_self_*` session, same settings on **Windows GPU (baseline)** vs **Google Colab GPU**. Do not upload participant data until consent/DPIA covers cloud processing.

## Recommended test clip

| Item | Value |
|------|--------|
| Source session | `sessions/NA_self_20260516_20260516T103935Z/` |
| Full recording | `recording.mp4` — **307 MB**, **20 min** (1200 s), 1280×720 @ 30 fps |
| Benchmark clip | **60 s → 360 s** (skip first minute for camera settle; **5 min** wall time) |
| Output clip file | `benchmark_5min.mp4` (~75 MB with stream copy) |
| Expected rows | ~**1200** at 4 fps (±10% after low FaceScore drops) |
| Schema QA reference | `sessions/longwork3_20260513T131959Z/facial_au.csv` — **688 columns**, 1255 rows (5 min Windows run) |

**Why not upload the full 20 min file to Colab?** Upload time dominates; a pre-cut 5 min clip keeps upload + GPU time comparable to Windows.

### Create the clip (Mac, before USB/Colab upload)

```bash
cd ~/thesis-phase1/sessions/NA_self_20260516_20260516T103935Z
ffmpeg -y -ss 60 -i recording.mp4 -t 300 -c copy benchmark_5min.mp4
ls -lh benchmark_5min.mp4
```

Copy the **whole session folder** (or at minimum `benchmark_5min.mp4` + `polar.jsonl` for timestamp sanity) to the Windows PC and/or Colab.

## Self-test sessions inventory

| Session | `recording.mp4` | Duration | `facial_au.csv` (Windows) |
|---------|-----------------|----------|---------------------------|
| `NA_self_20260516_20260516T103935Z` | 307 MB | 20 min | — |
| `longwork3_20260513T131959Z` | 45 MB | 5 min | **16 MB** (688 cols) |
| `longwork2_20260513T091645Z` | 70 MB | 5 min | — |
| `mouse_longwork_20260513T181315Z` | 49 MB | 5 min | — |
| `mousetest_20260513T180856Z` | 12 MB | 1 min | — |
| `test10_20260513T090907Z` | 12 MB | 1 min | — |
| `timertest2_20260513T183356Z` | 11 MB | 1 min | — |
| `timertest_20260513T182447Z` | 905 KB | 5 s | — |

Only **`longwork3_*`** currently has a Windows `facial_au.csv` for column-order QA; use it to validate Colab output schema, not timing (different participant/clip).

## Shared extraction settings (parity)

| Setting | Value |
|---------|--------|
| `identity_model` | `None` |
| Subsample | **4 fps** |
| Models | retinaface, mobilefacenet, xgb, resmasknet, img2pose |
| `batch_size` | **1** on Windows (12 GB RAM); try **4** on Colab T4 if VRAM allows |
| Output columns | **688** |

Repo script `extract_facial_aus.py` was aligned to these defaults (`identity_model=None`, `--fps` default 4).

---

## Windows baseline (GPU PC)

```powershell
C:\Users\windo\venvs\aubatch\Scripts\activate
$env:CUDA_VISIBLE_DEVICES = "0"
cd F:\AUbatch\scripts

# Copy session (with benchmark_5min.mp4) to incoming first, e.g.:
# F:\AUbatch\incoming\NA_self_20260516_20260516T103935Z\

# Chunked/resumable (preferred for long runs):
python extract_facial_aus_chunked.py F:\AUbatch\incoming\NA_self_20260516_20260516T103935Z --duration 300 --no-cleanup

# Or single-shot on the 5-min clip only (rename or --input):
python extract_facial_aus.py F:\AUbatch\incoming\NA_self_20260516_20260516T103935Z --input benchmark_5min.mp4 --output facial_au_win.csv --fps 4 --batch-size 1
```

Monitor (second window):

```powershell
nvidia-smi -l 5
Get-Content F:\AUbatch\incoming\NA_self_20260516_20260516T103935Z\au_chunks\chunk_000.log -Wait -Tail 20
```

Validate:

```powershell
(Get-Content F:\AUbatch\incoming\NA_self_20260516_20260516T103935Z\facial_au.csv | Measure-Object -Line).Lines
# Expect ~1200 lines (+ header) for 5 min @ 4 fps
```

Record from chunk logs or console: **total wall seconds**, **rows written**, **sec/frame** (= elapsed / rows).

---

## Google Colab

### Fresh session — upload to `MyDrive/THESIS/`

From the repo `colab/` folder, put these on **Google Drive**:

| Drive path | Source on Mac |
|------------|-----------------|
| `MyDrive/THESIS/au_benchmark_colab.ipynb` | Open in Colab (File → Upload notebook) |
| `MyDrive/THESIS/au_benchmark_colab.py` | `~/thesis-phase1/colab/au_benchmark_colab.py` |
| `MyDrive/THESIS/reference_facial_au_header.csv` | `~/thesis-phase1/colab/reference_facial_au_header.csv` (optional) |
| `MyDrive/THESIS/NA_self_20260516_20260516T103935Z/` | Whole session folder incl. `benchmark_5min.mp4` |

**Notebook paths (§3):**

```python
SESSION_DIR = '/content/drive/MyDrive/THESIS/NA_self_20260516_20260516T103935Z'
BENCHMARK_SCRIPT = Path('/content/drive/MyDrive/THESIS/au_benchmark_colab.py')
```

1. **Runtime → Change runtime type → T4 GPU → 2025.07** (Python 3.10/3.11; avoid Latest/2026 if pip fails).
2. Run cells in order: GPU check → install **2b** → verify **2c** → Drive mount **§3** → benchmark **§4** → QA **§5**.
3. Re-upload `au_benchmark_colab.py` (and re-import the notebook) whenever the repo `colab/` files change.

### Colab install (2025/2026) — not the Windows pins

The Windows `aubatch` venv uses `numpy<1.24`, `scipy<1.14`, and `opencv-python==4.8.1.78` because **nltools** (py-feat dependency) and **py-feat** (`scipy.integrate.simps`) conflict with newer stacks.

**What usually breaks on Colab**

| Symptom | Likely cause |
|---------|----------------|
| `metadata-generation-failed` / `python setup.py egg_info` after **~38 MB** download | Pip is building **scipy** (or **numpy**) **from source** instead of using a wheel |
| Same error right after `Collecting py-feat` | Resolver pulls **nltools**, which requires **`numpy<1.24`** — no wheel on **Python 3.12** → pip tries to compile old numpy |
| `ImportError: cannot import name 'simps' from 'scipy.integrate'` | **scipy ≥ 1.14** (simps renamed to `simpson`) |
| `AttributeError: _ARRAY_API not found` (often via **tables** → **numexpr**) | Colab **numpy 2.x** with old **numexpr** and/or **pytables** (`pip install tables`). Trace: `nltools` → `attempt_to_import('tables')` → `tables/file.py` → `numexpr`. Upgrade both (`numexpr>=2.10.1`, `tables>=3.10.1`) or uninstall `tables` (not used by `detect_video`) |
| `ModuleNotFoundError: No module named 'pynv'` on `from feat import Detector` | **nltools** imports `pynv` at package load; skipped when installing with `--no-deps` |

**Do not** use the one-liner `pip install py-feat` on Python 3.12 without the workaround below.

#### 1) Diagnostic — which package failed?

Run in a Colab cell (verbose pip; last lines name the failing package):

```python
!pip install -v py-feat 2>&1 | grep -E 'Collecting |Downloading |Preparing metadata|error:|subprocess-exited'
```

Typical output ends with `Collecting nltools` then `numpy<1.24` or `Preparing metadata (setup.py)` on **scipy** / **numpy**.

#### 2) Runtime (do this first if you are on Python 3.12)

**Runtime → Change runtime type → Hardware accelerator: T4 GPU → Runtime version: Python 3.10** (3.11 also works). **Disconnect and reconnect**, then re-run the notebook from the GPU check cell.

#### 3) Working install (copy-paste — matches `au_benchmark_colab.ipynb`)

Keeps Colab’s preinstalled **torch/CUDA**. **Pins numpy 1.26.x** (py-feat 0.6.1 uses `np.mat`, removed in NumPy 2.0), then **scipy 1.13.1**, then **py-feat** with `--no-deps`.

```python
# REQUIRED on Colab: numpy 1.x (py-feat 0.6.1 calls np.mat)
!pip install -q --force-reinstall --only-binary=numpy,scipy "numpy>=1.26.4,<2" "scipy==1.13.1"
!pip uninstall -y numexpr opencv-python-headless 2>/dev/null || true
!pip install -q --force-reinstall --no-deps "numexpr==2.8.4" "opencv-python-headless==4.8.1.78"
!pip install -q pandas tqdm

!pip install -q --no-deps "py-feat==0.6.1" "nltools==0.5.1"

!pip install -q --no-deps "tables==3.9.2" pynv "pywavelets>=0.3.0" "h5py>=2.7.0" "Pillow>=6.0.0" \
    "scikit-learn>=1.2" "scikit-image>=0.19" "joblib" "seaborn>=0.7.0" "matplotlib>=2.1" \
    "easing-functions" "celluloid" "kornia" "av>=9.2.0" "xgboost>=1.6.0" \
    nibabel nilearn
```

**Hotfix NOW** (copy-paste if benchmark subprocess fails with `_ARRAY_API` after verify looked OK):

```python
# Upgrade numpy-2-compatible numexpr + pytables (fixes nltools → tables → numexpr chain)
!pip install -q --upgrade "numexpr>=2.10.1" "tables>=3.10.1"
!pip install -q pynv
```

**Verify** (run immediately after hotfix):

```python
import numpy as np, numexpr, scipy.integrate as si, pynv
try:
    import tables
    tv = tables.__version__
except AttributeError as e:
    raise RuntimeError('Still broken — try uninstall: !pip uninstall -y tables') from e
except ImportError:
    tv = 'not installed (OK for detect_video)'
from feat import Detector
assert hasattr(si, 'simps')
Detector()
print('numpy', np.__version__, '| numexpr', numexpr.__version__, '| tables', tv)
print('py-feat Detector OK')
```

**Emergency** (if hotfix still fails — `tables` is optional for AU video extraction):

```python
!pip uninstall -y tables
```

Then re-run verify; `nltools` sets `tables=None` when import fails with `ImportError` only (a **broken** install must be upgraded or removed).

**Which stack for the 688-column speed pilot?**

| Approach | When to use |
|----------|-------------|
| **numpy 1.26.x + numexpr 2.10 + tables 3.10** (notebook cell **2f**) | **Safest** for comparing sec/frame to Windows (numpy 1.23.x); same BLAS habits, fewer binary surprises |
| ~~Colab numpy 2.x~~ | **Do not use** with py-feat 0.6.1 — crashes with ``np.mat was removed in NumPy 2.0`` during `detect_video` |

Then re-run the verify block in §4 below. **`pynv`** is the NeuroVault API client; **not used** by `Detector.detect_video`, but **required** because `nltools.data.brain_data` does `from pynv import Client` at import time and `feat` pulls `nltools` on `from feat import Detector`. **`tables`** is only used for legacy `Brain_Data` HDF loads in nltools; **py-feat `detect_video` does not call it**, but import still executes `attempt_to_import('tables')` unless the package is missing.

**Python 3.10 / 3.11 shortcut** (pin numpy like Windows family):

```python
!pip install -q --force-reinstall --only-binary=numpy,scipy "numpy>=1.26.4,<2" "scipy==1.13.1"
!pip install -q --upgrade "numexpr>=2.10.1" "tables>=3.10.1"
# then re-run the full 2b install block (scipy/py-feat/deps) if needed
```

Do **not** reinstall `torch` / `torchvision` in Colab.

Use **py-feat==0.6.1** on Colab (matches Windows; avoids 0.6.2 `representation_model` KeyError). Do not use 0.6.2 unless you run the notebook patch cell **2g**.

#### 4) Minimal verify

```python
import numexpr
import numpy as np
import scipy.integrate as si
import pynv
try:
    import tables
    tv = tables.__version__
except ImportError:
    tv = 'not installed'
from feat import Detector
assert hasattr(si, 'simps')
Detector()
print('numpy', np.__version__, '| numexpr', numexpr.__version__, '| tables', tv)
print('pynv', getattr(pynv, '__version__', 'ok'), '| py-feat OK')
```

#### 5) If install still fails

- **scipy / simps** → `!pip install -q --only-binary=scipy "scipy==1.13.1"`
- **numexpr / `_ARRAY_API`** → `!pip install -q --upgrade "numexpr>=2.10.1" "tables>=3.10.1"` (never `numexpr<2.8.5` on Colab numpy 2.x); or `!pip uninstall -y tables`
- **speed benchmark parity** → cell **2f** / `numpy>=1.26.4,<2` before timing runs
- **missing pynv** → `!pip install -q pynv`
- **Still building numpy/scipy from source** → switch runtime to **Python 3.10** and use the shortcut block above
- **CUDA OOM** during benchmark → `BATCH_SIZE = 1` in the notebook

**Methodology note:** Colab may run **numpy 2.x** vs Windows **1.23.x**. For this **speed + schema pilot**, that is acceptable if output still has **688 columns** and `timestamp_utc`; compare sec/frame and column QA, not AU values bit-for-bit.

**Run from Drive (matches notebook §4):**

```bash
!python /content/drive/MyDrive/THESIS/au_benchmark_colab.py \
  --session-dir /content/drive/MyDrive/THESIS/NA_self_20260516_20260516T103935Z \
  --input benchmark_5min.mp4 \
  --fps 4 \
  --batch-size 4 \
  --no-chunks
```

Timing log: `sessions/.../colab_work/benchmark_timing.jsonl`  
Output CSV: `facial_au_colab.csv` in the session folder.

Schema QA (optional — upload header only from longwork3):

```python
# After run: compare column count
import pandas as pd
df = pd.read_csv(f"{SESSION}/facial_au_colab.csv", nrows=1)
assert len(df.columns) == 688
```

---

## Comparison table (fill in after both runs)

| Metric | Windows (`facial_au.csv` or `facial_au_win.csv`) | Colab (`facial_au_colab.csv`) |
|--------|--------------------------------------------------|-------------------------------|
| Machine / GPU | GTX 1660 SUPER | Colab T4 (note type) |
| Clip | `benchmark_5min.mp4` (60–360 s) | Same file |
| `fps` / `batch_size` | 4 / 1 | 4 / 4 (or 1 if OOM) |
| Wall time (s) | | |
| Rows | ~1200 | |
| sec/frame | wall / rows | from `benchmark_timing.jsonl` |
| Columns | 688 | 688 |
| `timestamp_utc` present | yes | yes |

**Decision rule (pilot):** If Colab sec/frame is within ~2× of Windows and upload+run fits your workflow, cloud is viable for formal study **after** consent/DPIA text is updated. If Colab is >3× slower or unstable, stay on Windows GPU for Phase 1 scale-up.

---

## Ethics note (pilot vs formal study)

- **Now:** Benchmark only on **your own** `NA_self_*` (and similar self-captured) sessions; no participant uploads to Google.
- **Future consent/DPIA wording (suggestion):**  
  *“Facial video may be processed on a secure university Windows workstation or, where documented in the DPIA, on Google Colab (Google Cloud) using your de-identified session recording solely to extract Action Unit time series; raw video is not retained by the cloud provider beyond the processing session you control.”*

---

## Files in this folder

| File | Purpose |
|------|---------|
| `au_benchmark_colab.ipynb` | Colab notebook (install, GPU, upload, run, QA) |
| `au_benchmark_colab.py` | Same logic as `%run` script |
| `BENCHMARK.md` | This guide |
| `reference_facial_au_header.csv` | First line of `longwork3` Windows CSV for column-order QA |
