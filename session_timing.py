"""
Recording anchor resolution and facial AU timestamp enrichment.

Used by colab/au_benchmark_colab.py, extract_facial_aus.py, and analysis repair paths.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHUNK_RE = re.compile(r"chunk_(\d+)", re.IGNORECASE)


def format_timestamp_utc(dt: datetime) -> str:
    """UTC ISO string with Z suffix (pandas-friendly)."""
    s = dt.astimezone(timezone.utc).isoformat()
    return s.replace("+00:00", "Z")


def parse_iso_utc(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_recording_anchor(
    session_dir: Path,
    cli_anchor: str | None = None,
) -> tuple[datetime, str]:
    """Return (anchor UTC, human-readable source label)."""
    session_dir = session_dir.expanduser().resolve()

    sidecar = session_dir / "recording_start.txt"
    if sidecar.is_file():
        return parse_iso_utc(sidecar.read_text(encoding="utf-8")), "recording_start.txt"

    if cli_anchor:
        return parse_iso_utc(cli_anchor), "--recording-start CLI flag"

    name = session_dir.name
    if "_" in name:
        ts_part = name.rsplit("_", 1)[-1]
        try:
            dt = datetime.strptime(ts_part, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return dt, "session directory name suffix"
        except ValueError:
            pass

    polar = session_dir / "polar.jsonl"
    if polar.exists():
        with polar.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    ts = r.get("timestamp_utc")
                    if ts:
                        return parse_iso_utc(ts), "polar.jsonl (first row)"
                except Exception:
                    pass
                break

    raise FileNotFoundError(
        f"Cannot resolve recording anchor for {session_dir}. "
        "Add recording_start.txt, pass --recording-start, use a session folder name "
        "ending in YYYYMMDDTHHMMSSZ, or include polar.jsonl."
    )


def ffprobe_fps(video_path: Path) -> float:
    """Native video FPS via ffprobe; falls back to OpenCV."""
    video_path = Path(video_path)
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        rate = proc.stdout.strip().splitlines()[0].strip()
        if "/" in rate:
            num, den = rate.split("/", 1)
            return float(num) / float(den)
        return float(rate)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, IndexError):
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video for FPS probe: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.release()
        return float(fps)


def parse_chunk_index_from_input(value) -> int | None:
    if value is None:
        return None
    m = CHUNK_RE.search(str(value))
    return int(m.group(1)) if m else None


def enrich_facial_au_timestamps(
    df,
    *,
    native_fps: float,
    anchor_utc: datetime,
    chunk_offset_sec: float = 0.0,
    chunk_index: int | None = None,
    chunk_seconds: int = 300,
):
    """
    Set approx_time (seconds from recording t=0) and timestamp_utc for every row.

    approx_time = chunk_offset_sec + frame / native_fps
    """
    import pandas as pd

    out = df.copy()
    if "frame" not in out.columns:
        raise ValueError("Missing 'frame' column in facial AU dataframe")

    if chunk_index is None and "input" in out.columns and len(out):
        chunk_index = parse_chunk_index_from_input(out["input"].iloc[0])

    if chunk_offset_sec <= 0 and chunk_index is not None:
        chunk_offset_sec = float(chunk_index * chunk_seconds)

    frames = pd.to_numeric(out["frame"], errors="coerce")
    approx = chunk_offset_sec + (frames / float(native_fps))
    out["approx_time"] = approx
    out["timestamp_utc"] = approx.apply(
        lambda t: format_timestamp_utc(anchor_utc + timedelta(seconds=float(t)))
        if pd.notna(t)
        else None
    )
    return out


def repair_timestamps_from_chunks(
    df,
    session_dir: Path,
    *,
    video_name: str = "recording.mp4",
    chunk_seconds: int = 300,
    cli_anchor: str | None = None,
):
    """Re-enrich a merged CSV (e.g. after GPU run with null timestamps)."""
    import pandas as pd

    session_dir = session_dir.expanduser().resolve()
    anchor, _src = resolve_recording_anchor(session_dir, cli_anchor=cli_anchor)
    native_fps = ffprobe_fps(session_dir / video_name)

    if "input" not in df.columns:
        return enrich_facial_au_timestamps(
            df,
            native_fps=native_fps,
            anchor_utc=anchor,
            chunk_offset_sec=0.0,
            chunk_seconds=chunk_seconds,
        )

    parts = []
    chunk_ids = df["input"].map(parse_chunk_index_from_input)
    for chunk_idx in sorted(chunk_ids.dropna().unique()):
        mask = chunk_ids == chunk_idx
        group = df.loc[mask]
        parts.append(
            enrich_facial_au_timestamps(
                group,
                native_fps=native_fps,
                anchor_utc=anchor,
                chunk_offset_sec=float(chunk_idx) * chunk_seconds,
                chunk_index=int(chunk_idx),
                chunk_seconds=chunk_seconds,
            )
        )
    unknown = chunk_ids.isna()
    if unknown.any():
        parts.append(
            enrich_facial_au_timestamps(
                df.loc[unknown],
                native_fps=native_fps,
                anchor_utc=anchor,
                chunk_offset_sec=0.0,
                chunk_seconds=chunk_seconds,
            )
        )
    return pd.concat(parts, ignore_index=True)


def qa_timestamps(df, *, strict_monotonic: bool = False) -> None:
    import pandas as pd

    n_ts = int(df["timestamp_utc"].isna().sum()) if "timestamp_utc" in df.columns else len(df)
    n_ap = int(df["approx_time"].isna().sum()) if "approx_time" in df.columns else len(df)
    if n_ts or n_ap:
        raise ValueError(
            f"timestamp QA failed: {n_ts} null timestamp_utc, {n_ap} null approx_time"
        )
    ts = pd.to_datetime(df["timestamp_utc"], utc=True, format="ISO8601")
    if strict_monotonic and not ts.is_monotonic_increasing:
        raise ValueError("timestamp_utc is not monotonic increasing")
