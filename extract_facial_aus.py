"""
extract_facial_aus.py

Extracts facial Action Units (AUs), emotions, head pose, and landmarks from a
session's recording.mp4 using py-feat with XGBoost AU detection.

Designed to be run post-session, on the operator's command. Not part of the
real-time session pipeline. Processes the video at a configurable subsample
rate (default 4 fps) to balance temporal resolution and processing time.

Output: facial_au.csv in the same directory as the input video.
Each row is one frame with:
  - frame, time_seconds, timestamp_utc (computed from recording start)
  - 20 Action Unit intensities (AU01_r through AU45_r)
  - 7 emotion probabilities (happiness, sadness, anger, surprise, disgust, fear, neutral)
  - Head pose: pitch, roll, yaw
  - Face bounding box and detection confidence

Usage:
    python extract_facial_aus.py /path/to/session_dir
    python extract_facial_aus.py /path/to/session_dir --fps 4
    python extract_facial_aus.py /path/to/session_dir --start 30 --duration 30
"""

import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
os.environ['OMP_NUM_THREADS'] = '1'
import torch
torch.set_grad_enabled(False)
import argparse
import json
import sys
import time
from pathlib import Path

from session_timing import (
    enrich_facial_au_timestamps,
    ffprobe_fps,
    qa_timestamps,
    resolve_recording_anchor,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_dir",
                        help="Path to the session directory containing recording.mp4")
    parser.add_argument("--fps", type=int, default=4,
                        help="Subsample rate in frames per second (default 4)")
    parser.add_argument("--start", type=float, default=0,
                        help="Start offset in seconds (for testing on a clip)")
    parser.add_argument("--duration", type=float, default=None,
                        help="Duration to process in seconds (for testing)")
    parser.add_argument("--input", type=str, default="recording.mp4",
                        help="Video filename inside session directory")
    parser.add_argument("--output", type=str, default="facial_au.csv",
                        help="Output CSV filename inside session directory")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Frames per batch (1 is safest on low-RAM machines)")
    args = parser.parse_args()

    session_dir = Path(args.session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        print(f"ERROR: {session_dir} is not a directory")
        sys.exit(1)

    video_path = session_dir / args.input
    if not video_path.exists():
        print(f"ERROR: {video_path} not found")
        sys.exit(1)

    output_path = session_dir / args.output

    # Lazy import to keep --help fast
    print("Loading py-feat...")
    from feat import Detector

    print(f"Initializing detector (XGBoost AU model)...")
    detector = Detector(
        face_model="retinaface",
        landmark_model="mobilefacenet",
        au_model="xgb",
        emotion_model="resmasknet",
        facepose_model="img2pose",
        identity_model=None,  # avoids O(N^2) identity matrix on long videos
    )

    anchor_utc, anchor_source = resolve_recording_anchor(session_dir)
    print(f"[INFO] timestamp_utc anchor: {anchor_source} -> {anchor_utc.isoformat()}")

    print(f"Input video:            {video_path}")
    print(f"Output CSV:             {output_path}")
    print(f"Subsample rate:         {args.fps} fps")
    if args.start > 0 or args.duration:
        end_str = f" through {args.start + (args.duration or 0):.0f}s" if args.duration else " through end"
        print(f"Range:                  {args.start:.0f}s{end_str}")
    print()

    # Probe video to get total duration and native fps
    print("Probing video...")
    import cv2
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"ERROR: cv2 could not open {video_path}")
        sys.exit(1)
    native_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    native_duration = total_frames / native_fps if native_fps > 0 else 0
    cap.release()

    print(f"  Native FPS:           {native_fps:.1f}")
    print(f"  Total frames:         {total_frames}")
    print(f"  Native duration:      {native_duration:.1f}s ({native_duration/60:.1f}m)")
    print()

    # Compute the frames we'll actually sample
    skip_frames = max(1, int(round(native_fps / args.fps)))
    effective_fps = native_fps / skip_frames

    start_frame = int(args.start * native_fps)
    if args.duration:
        end_frame = min(total_frames, int((args.start + args.duration) * native_fps))
    else:
        end_frame = total_frames

    frames_to_process = list(range(start_frame, end_frame, skip_frames))
    n_frames = len(frames_to_process)
    estimated_duration = n_frames * 1.0  # very rough estimate: 1s/frame on CPU

    print(f"Effective sample rate:  {effective_fps:.2f} fps (every {skip_frames}th frame)")
    print(f"Frames to process:      {n_frames}")
    print(f"Estimated processing:   {estimated_duration:.0f}s ({estimated_duration/60:.1f}m)")
    print()

    # Process frame by frame
    # py-feat's detect_video has a skip_frames argument that does exactly what we want.
    # We use it for efficiency over manual frame-by-frame iteration.
    print("Running detection...")
    start_time = time.monotonic()

    fex = detector.detect_video(
        video_path=str(video_path),
        skip_frames=skip_frames,
        batch_size=args.batch_size,
    )

    elapsed = time.monotonic() - start_time
    print(f"Detection complete in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    print(f"Frames analyzed: {len(fex)}")
    print()

    native_fps = ffprobe_fps(video_path)
    fex = enrich_facial_au_timestamps(
        fex,
        native_fps=native_fps,
        anchor_utc=anchor_utc,
        chunk_offset_sec=0.0,
    )

    # Trim to requested wall-clock window (approx_time is seconds from video t=0)
    if args.start > 0 or args.duration:
        t_end = args.start + args.duration if args.duration else float("inf")
        before = len(fex)
        fex = fex[(fex["approx_time"] >= args.start) & (fex["approx_time"] < t_end)]
        print(
            f"Applied --start/--duration filter: {before} -> {len(fex)} rows "
            f"({args.start:.0f}s to {t_end:.0f}s). "
            "NOTE: full video was still decoded; use a pre-trimmed clip for fair timing."
        )
        print()

    qa_timestamps(fex)

    # Write output
    print(f"Writing CSV: {output_path}")
    fex.to_csv(output_path, index=False)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Output size: {size_mb:.1f} MB")
    print(f"Rows:        {len(fex)}")
    print(f"Columns:     {len(fex.columns)}")
    print()
    print("Extraction complete.")


if __name__ == "__main__":
    main()
