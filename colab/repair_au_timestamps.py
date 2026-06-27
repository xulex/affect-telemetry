#!/usr/bin/env python3
"""Repair null approx_time / timestamp_utc in an existing facial AU CSV (no GPU)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_timing import (  # noqa: E402
    qa_timestamps,
    repair_timestamps_from_chunks,
    resolve_recording_anchor,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session-dir", type=Path, required=True)
    p.add_argument("--input-csv", default="facial_au_azure.csv")
    p.add_argument("--output", default=None, help="Default: overwrite input CSV")
    p.add_argument("--recording-start", default=None)
    p.add_argument("--chunk-seconds", type=int, default=300)
    args = p.parse_args()

    session_dir = args.session_dir.expanduser().resolve()
    in_path = session_dir / args.input_csv
    out_path = Path(args.output) if args.output else in_path

    import pandas as pd

    df = pd.read_csv(in_path)
    anchor, src = resolve_recording_anchor(session_dir, cli_anchor=args.recording_start)
    print(f"[INFO] timestamp_utc anchor: {src} -> {anchor.isoformat()}")

    fixed = repair_timestamps_from_chunks(
        df,
        session_dir,
        chunk_seconds=args.chunk_seconds,
        cli_anchor=args.recording_start,
    )
    qa_timestamps(fixed)
    fixed.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(fixed)} rows)")
    print(
        "Sample:",
        fixed[["frame", "approx_time", "timestamp_utc"]].head(2).to_string(index=False),
    )


if __name__ == "__main__":
    main()
