"""
Colab / cloud AU benchmark — mirrors extract_facial_aus.py + 5-min chunking.

Run in Google Colab (GPU runtime). Do not use for participant data until
consent/DPIA covers cloud processing.

Usage (Colab):
    %run au_benchmark_colab.py
Or:
    python au_benchmark_colab.py --session-dir /content/NA_self_... --fps 4
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

# Repo root on sys.path for session_timing (Mac, Azure, Colab)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from session_timing import (  # noqa: E402
    enrich_facial_au_timestamps,
    ffprobe_fps,
    qa_timestamps,
    resolve_recording_anchor,
)

# Inference-safe defaults (match repo extract_facial_aus.py)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Windows longwork CSV with identity embeddings: 688 cols.
# Current pipeline uses identity_model=None (memory): 175 cols (no Identity_* block).
EXPECTED_COLUMNS_WITH_IDENTITY = 688
EXPECTED_COLUMNS_NO_IDENTITY = 175
DEFAULT_FPS = 4
CHUNK_SECONDS = 300  # 5 minutes


def default_work_dir(session_dir: Path) -> Path:
    """Keep chunk/timing I/O off Google Drive (slow / flaky); CSV still writes to session."""
    session_dir = session_dir.resolve()
    if str(session_dir).startswith("/content/drive"):
        return Path("/content/colab_work") / session_dir.name
    return session_dir / "colab_work"


def probe_video(path: Path) -> tuple[float, float, int]:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / native_fps if native_fps > 0 else 0.0
    cap.release()
    return native_fps, duration, total_frames


def ffmpeg_segment(input_mp4: Path, out_mp4: Path, start_s: float, duration_s: float) -> None:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_s),
        "-i",
        str(input_mp4),
        "-t",
        str(duration_s),
        "-c",
        "copy",
        str(out_mp4),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def gpu_report() -> dict:
    import torch

    info = {
        "cuda_available": torch.cuda.is_available(),
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "torch": torch.__version__,
    }
    return info


def _patch_pyfeat_identity_none() -> bool:
    """py-feat 0.6.2: identity_model=None raises KeyError('representation_model').

    Upstream bug: the None branch references PRETRAINED_MODELS['representation_model']
    (key does not exist). Patch pretrained.py in-place, then detector.py for empty identity.
  """
    import importlib
    import inspect
    import re
    from pathlib import Path

    import feat.pretrained as pt

    if getattr(pt, "_thesis_identity_none_patch", False):
        return True

    pre_path = Path(inspect.getfile(pt))
    text = pre_path.read_text(encoding="utf-8")
    patched = False

    if "thesis patch: skip identity" not in text:
        pattern = re.compile(
            r"(# Face Identity model\s+if identity_model is None:\s+)"
            r'raise ValueError\(\s+f"representation_model must be one of \{[^}]+\}"\s+\)',
            re.MULTILINE,
        )
        new_text, n = pattern.subn(
            r"\1pass  # thesis patch: skip identity (688-col Windows parity)",
            text,
            count=1,
        )
        if n == 0:
            needle = "PRETRAINED_MODELS['representation_model']"
            if needle in text:
                new_text = text.replace(
                    "if identity_model is None:\n"
                    "        raise ValueError(\n"
                    f'            f"representation_model must be one of {{[list(e.keys())[0] for e in {needle}]}}"\n'
                    "        )",
                    "if identity_model is None:\n"
                    "        pass  # thesis patch: skip identity (688-col Windows parity)",
                    1,
                )
                n = 1 if new_text != text else 0
        if n:
            pre_path.write_text(new_text, encoding="utf-8")
            importlib.reload(pt)
            patched = True
        else:
            print(
                "WARNING: feat/pretrained.py identity_model=None patch not applied. "
                "Try: pip install -q --no-deps py-feat==0.6.1 then restart runtime."
            )
            return False
    else:
        patched = True

    import feat.detector as det_mod

    det_path = Path(inspect.getfile(det_mod))
    det_text = det_path.read_text(encoding="utf-8")
    marker = "# thesis patch: identity_model=None"
    if marker not in det_text:
        det_text = det_text.replace(
            "    # IDENTITY MODEL\n"
            "    if self.info[\"identity_model\"] != identity:",
            (
                "    # IDENTITY MODEL\n"
                f"    {marker}\n"
                "    if identity is None:\n"
                "        self.identity_model = None\n"
                "        self.info[\"identity_model\"] = None\n"
                "        self.info[\"identity_model_columns\"] = []\n"
                "        self._empty_identity = pd.DataFrame()\n"
                "    elif self.info[\"identity_model\"] != identity:"
            ),
            1,
        )
        det_text = det_text.replace(
            "        + self.info[\"identity_model_columns\"]\n"
            "        + [\"input\"]\n",
            (
                "        + (self.info.get(\"identity_model_columns\") or [])\n"
                "        + [\"input\"]\n"
            ),
            1,
        )
        needle = (
            "        logging.info(\"detecting identity...\")\n\n"
            "        frame = convert_image_to_tensor(frame, img_type=\"float32\") / 255"
        )
        det_text = det_text.replace(
            needle,
            (
                "        if getattr(self, \"identity_model\", None) is None:\n"
                "            return facebox\n\n"
                "        logging.info(\"detecting identity...\")\n\n"
                "        frame = convert_image_to_tensor(frame, img_type=\"float32\") / 255"
            ),
            1,
        )
        det_text = det_text.replace(
            "        batch_output.compute_identities(\n"
            "            threshold=face_identity_threshold, inplace=True\n"
            "        )\n",
            (
                "        if self.info.get(\"identity_model_columns\"):\n"
                "            batch_output.compute_identities(\n"
                "                threshold=face_identity_threshold, inplace=True\n"
                "            )\n"
            ),
            1,
        )
        det_path.write_text(det_text, encoding="utf-8")
        importlib.reload(det_mod)

    pt._thesis_identity_none_patch = True
    return True


def load_detector(batch_size: int):
    import inspect

    import torch
    from feat import Detector

    torch.set_grad_enabled(False)
    model_kw = dict(
        face_model="retinaface",
        landmark_model="mobilefacenet",
        au_model="xgb",
        emotion_model="resmasknet",
        facepose_model="img2pose",
    )
    sig = inspect.signature(Detector.__init__)
    if "identity_model" in sig.parameters:
        if not _patch_pyfeat_identity_none():
            raise RuntimeError(
                "py-feat 0.6.2 cannot use identity_model=None without a patch. "
                "In Colab run: !pip install -q --no-deps py-feat==0.6.1 then Runtime → Restart, "
                "or re-copy colab/au_benchmark_colab.py from the repo and re-run."
            )
        detector = Detector(**model_kw, identity_model=None)
    else:
        detector = Detector(**model_kw)
    return detector, batch_size


def process_clip(
    detector,
    video_path: Path,
    fps: int,
    batch_size: int,
    chunk_label: str,
    log_path: Path,
):
    native_fps, duration, _ = probe_video(video_path)
    skip_frames = max(1, int(round(native_fps / fps)))
    n_expected = int(duration * fps)

    t0 = time.monotonic()
    fex = detector.detect_video(
        video_path=str(video_path),
        skip_frames=skip_frames,
        batch_size=batch_size,
    )
    elapsed = time.monotonic() - t0
    n_rows = len(fex)
    sec_per_frame = elapsed / n_rows if n_rows else float("nan")

    entry = {
        "chunk": chunk_label,
        "video": str(video_path),
        "duration_s": round(duration, 2),
        "fps_target": fps,
        "skip_frames": skip_frames,
        "rows": n_rows,
        "elapsed_s": round(elapsed, 2),
        "sec_per_frame": round(sec_per_frame, 4),
        "rows_per_sec": round(n_rows / elapsed, 4) if elapsed > 0 else None,
    }
    with log_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    print(json.dumps(entry, indent=2))
    return fex


def qa_columns(df, reference_header: Path | None) -> None:
    n = len(df.columns)
    has_identity = any(c.startswith("Identity_") for c in df.columns)
    expected = (
        EXPECTED_COLUMNS_WITH_IDENTITY
        if has_identity
        else EXPECTED_COLUMNS_NO_IDENTITY
    )
    print(
        f"Column count: {n} (expected {expected}"
        f"{' with identity embeddings' if has_identity else ', identity_model=None'})"
    )
    if n != expected:
        print(
            f"WARNING: column count mismatch (got {n}, expected {expected}"
            f"{' — old Windows facial_au.csv used 688 cols with Identity_*' if not has_identity else ''})"
        )
    if reference_header and reference_header.exists():
        ref_cols = reference_header.read_text().splitlines()[0].strip().split(",")
        out_cols = list(df.columns)
        if ref_cols == out_cols:
            print("Column names/order match reference header.")
        else:
            missing = set(ref_cols) - set(out_cols)
            extra = set(out_cols) - set(ref_cols)
            if missing:
                print(f"  Missing vs reference: {sorted(missing)[:10]}...")
            if extra:
                print(f"  Extra vs reference: {sorted(extra)[:10]}...")


def require_numpy1() -> None:
    """py-feat 0.6.1 uses np.mat (removed in NumPy 2.0)."""
    import numpy as np

    major = int(np.__version__.split(".")[0])
    if major >= 2:
        raise RuntimeError(
            f"NumPy {np.__version__} is incompatible with py-feat 0.6.1 (needs np.mat). "
            "In Colab run:\n"
            '  !pip install -q --force-reinstall --only-binary=numpy,scipy '
            '"numpy>=1.26.4,<2" "scipy==1.13.1"\n'
            "Then Runtime → Restart session, re-run install + verify cells."
        )


def _chunk_plan(total_duration: float, chunk_seconds: int) -> list[tuple[float, float, int]]:
    plan: list[tuple[float, float, int]] = []
    offset = 0.0
    idx = 0
    while offset < total_duration - 0.5:
        dur = min(chunk_seconds, total_duration - offset)
        plan.append((offset, dur, idx))
        offset += dur
        idx += 1
    return plan


def run_benchmark(
    session_dir: Path,
    video_name: str = "recording.mp4",
    fps: int = DEFAULT_FPS,
    batch_size: int = 4,
    chunk_seconds: int = CHUNK_SECONDS,
    use_chunks: bool = True,
    reference_csv: Path | None = None,
    output_name: str = "facial_au_colab.csv",
    work_dir: Path | None = None,
    show_progress: bool = False,
    resume_chunks: bool = True,
    recording_start_cli: str | None = None,
) -> Path:
    require_numpy1()
    session_dir = session_dir.expanduser().resolve()
    video_path = session_dir / video_name
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    work = (work_dir or default_work_dir(session_dir)).resolve()
    work.mkdir(parents=True, exist_ok=True)
    print("Work dir (local temp):", work, flush=True)
    log_path = work / "benchmark_timing.jsonl"
    if log_path.exists():
        log_path.unlink()

    anchor_utc, anchor_source = resolve_recording_anchor(
        session_dir, cli_anchor=recording_start_cli
    )
    print(
        f"[INFO] timestamp_utc anchor: {anchor_source} -> {anchor_utc.isoformat()}",
        flush=True,
    )
    print("GPU:", json.dumps(gpu_report(), indent=2))
    print("Session:", session_dir)

    import feat

    print("py-feat", getattr(feat, "__version__", "?"), flush=True)
    detector, batch_size = load_detector(batch_size)
    native_fps = ffprobe_fps(video_path)
    _, total_duration, _ = probe_video(video_path)
    print(f"Video native FPS (ffprobe): {native_fps:.4f}", flush=True)

    import pandas as pd

    chunks_dir = work / "chunks"
    if use_chunks and total_duration > chunk_seconds + 1:
        chunks_dir.mkdir(exist_ok=True)
        plan = _chunk_plan(total_duration, chunk_seconds)
        print(
            f"Chunked run: {len(plan)} x {chunk_seconds}s segments "
            f"({total_duration/60:.1f} min video @ {fps} fps)",
            flush=True,
        )

        chunk_iter = plan
        if show_progress:
            from tqdm.auto import tqdm

            chunk_iter = tqdm(plan, desc="Session chunks", unit="chunk")

        parts = []
        for offset, dur, idx in chunk_iter:
            label = f"chunk_{idx:03d}"
            clip = chunks_dir / f"{label}.mp4"
            chunk_csv = chunks_dir / f"{label}.csv"

            if resume_chunks and chunk_csv.is_file():
                print(f"Resume {label}: loading {chunk_csv.name}", flush=True)
                fex = pd.read_csv(chunk_csv)
            else:
                if not clip.is_file():
                    msg = f"Segmenting {offset:.0f}s + {dur:.0f}s -> {clip.name}"
                    if show_progress and hasattr(chunk_iter, "set_postfix_str"):
                        chunk_iter.set_postfix_str(f"ffmpeg {label}")  # type: ignore[attr-defined]
                    else:
                        print(msg, flush=True)
                    ffmpeg_segment(video_path, clip, offset, dur)
                elif not show_progress:
                    print(f"Using existing {clip.name}", flush=True)

                if show_progress and hasattr(chunk_iter, "set_postfix_str"):
                    chunk_iter.set_postfix_str(f"detect {label}")  # type: ignore[attr-defined]

                fex = process_clip(
                    detector,
                    clip,
                    fps,
                    batch_size,
                    chunk_label=label,
                    log_path=log_path,
                )
                fex.to_csv(chunk_csv, index=False)
                print(f"Checkpoint {chunk_csv.name} ({len(fex)} rows)", flush=True)

            fex = enrich_facial_au_timestamps(
                fex,
                native_fps=native_fps,
                anchor_utc=anchor_utc,
                chunk_offset_sec=offset,
                chunk_index=idx,
                chunk_seconds=chunk_seconds,
            )
            parts.append(fex)

        merged = pd.concat(parts, ignore_index=True)
    else:
        merged = process_clip(
            detector,
            video_path,
            fps,
            batch_size,
            chunk_label="full",
            log_path=log_path,
        )
        merged = enrich_facial_au_timestamps(
            merged,
            native_fps=native_fps,
            anchor_utc=anchor_utc,
            chunk_offset_sec=0.0,
            chunk_seconds=chunk_seconds,
        )

    qa_timestamps(merged)

    out_path = session_dir / output_name
    print(f"Writing {out_path} ...", flush=True)
    merged.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(merged)} rows, {len(merged.columns)} cols)", flush=True)
    qa_columns(merged, reference_csv)
    return out_path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session-dir", type=Path, required=True)
    p.add_argument("--input", default="recording.mp4")
    p.add_argument("--fps", type=int, default=DEFAULT_FPS)
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--no-chunks", action="store_true")
    p.add_argument("--reference-csv", type=Path, default=None)
    p.add_argument("--output", default="facial_au_colab.csv")
    p.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Temp chunks/timing (default: /content/colab_work/<session> when session on Drive)",
    )
    p.add_argument(
        "--progress",
        action="store_true",
        help="tqdm chunk bar + py-feat frame bar (best in notebook, not subprocess)",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Reprocess all chunks even if chunk_XXX.csv exists",
    )
    p.add_argument(
        "--recording-start",
        default=None,
        help="ISO8601 UTC anchor when recording_start.txt is missing",
    )
    args = p.parse_args()

    ref = args.reference_csv
    if ref is None:
        candidate = Path("/content/reference_facial_au_header.csv")
        if candidate.exists():
            ref = candidate

    run_benchmark(
        args.session_dir,
        video_name=args.input,
        fps=args.fps,
        batch_size=args.batch_size,
        use_chunks=not args.no_chunks,
        reference_csv=ref,
        output_name=args.output,
        work_dir=args.work_dir,
        show_progress=args.progress,
        resume_chunks=not args.no_resume,
        recording_start_cli=args.recording_start,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
