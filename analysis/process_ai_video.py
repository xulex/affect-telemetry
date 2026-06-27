"""
process_ai_video.py
===================

Layer-2 AI usage detection: sample frames from recording.mp4 during browser
focus windows (from ai_usage_report.json) and OCR for known AI assistant
domains and on-screen UI labels (Gemini, ChatGPT, Claude, …).

USAGE
-----
    python process_ai_video.py SESSION_DIR
    python process_ai_video.py SESSION_DIR --sample-every 15 --write-json

Requires on the host: ffmpeg, tesseract, pillow, pytesseract.
Run detect_ai_usage.py first (or pass --write-json) so ai_usage_report.json exists.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image, ImageOps
    import pytesseract
except ImportError:
    print("ERROR: install pillow and pytesseract (see azure/install_video_ubuntu.sh)",
          file=sys.stderr)
    sys.exit(1)


# URL / domain patterns (applied after OCR normalization).
AI_DOMAIN_PATTERNS = [
    (re.compile(p, re.I), label)
    for p, label in [
        (r'chatgpt\.com', 'chatgpt'),
        (r'chat\.openai', 'chatgpt'),
        (r'openai\.com/chat', 'chatgpt'),
        (r'claude\.ai', 'claude_web'),
        (r'anthropic\.com', 'anthropic'),
        (r'gemini\.google', 'gemini'),
        (r'gemini\s*google', 'gemini'),          # OCR: "gemini google.com"
        (r'geminigoogl', 'gemini'),                # OCR: collapsed spacing
        (r'copilot\.microsoft', 'copilot'),
        (r'bing\.com/chat', 'copilot'),
        (r'perplexity\.ai', 'perplexity'),
        (r'cursor\.com', 'cursor_web'),
        (r'poe\.com', 'poe'),
    ]
]

# Visible UI chrome (works when URL bar is hidden or poorly OCR'd).
AI_UI_PATTERNS = [
    (re.compile(p, re.I), label)
    for p, label in [
        (r'ask\s+gemini', 'gemini'),
        (r'meet\s+gemini', 'gemini'),
        (r'\bgemini\b.*\bassistant\b', 'gemini'),
        (r'\bchat\s*gpt\b', 'chatgpt'),
        (r'\bclaude\b', 'claude_web'),
        (r'microsoft\s+copilot', 'copilot'),
        (r'\bperplexity\b', 'perplexity'),
    ]
]

RESEARCH_HINTS = re.compile(
    r'wikipedia|google\.com/search|scholar\.linkedin|halestrom|meridian\s+consulting',
    re.I,
)

# Fraction of frame height to OCR (name, y0, y1).
OCR_BANDS = (
    ('url_bar', 0.00, 0.10),
    ('header', 0.00, 0.28),
    ('main', 0.05, 0.45),
)


def parse_ts(s: str) -> float:
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    return datetime.fromisoformat(s).timestamp()


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def video_duration_sec(video: Path) -> float:
    out = subprocess.check_output(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(video)],
        text=True,
    ).strip()
    return float(out)


def resolve_time_base(session_dir: Path) -> tuple[float, str]:
    rec_start = session_dir / 'recording_start.txt'
    if rec_start.is_file():
        return parse_ts(rec_start.read_text(encoding='utf-8')), 'recording_start.txt'
    meta = load_json(session_dir / 'session_metadata.json') or {}
    if meta.get('session_start_utc'):
        return parse_ts(meta['session_start_utc']), 'session_metadata.session_start_utc'
    raise SystemExit(f'No recording_start.txt or session_start_utc in {session_dir}')


def session_start_ts(session_dir: Path) -> float | None:
    meta = load_json(session_dir / 'session_metadata.json') or {}
    if meta.get('session_start_utc'):
        return parse_ts(meta['session_start_utc'])
    return None


def sample_points(start: float, end: float, every: float) -> list[float]:
    if end <= start:
        return [start]
    span = end - start
    step = every if span > 90 else min(every, 12.0)
    pts = [start + 2.0, (start + end) / 2, max(start, end - 3.0)]
    t = start
    while t <= end:
        pts.append(t)
        t += step
    return sorted(set(max(0.0, p) for p in pts if p < end))


def extract_frame(video: Path, offset_sec: float, out_jpg: Path) -> bool:
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-ss', f'{offset_sec:.3f}',
        '-i', str(video),
        '-frames:v', '1',
        '-q:v', '2',
        str(out_jpg),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return out_jpg.is_file() and out_jpg.stat().st_size > 0
    except subprocess.CalledProcessError:
        return False


def _prep_for_ocr(img: Image.Image, scale: float = 2.0) -> Image.Image:
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g)
    if scale != 1.0:
        w, h = g.size
        g = g.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    return g


def ocr_bands(jpg: Path) -> dict[str, str]:
    img = Image.open(jpg)
    w, h = img.size
    out: dict[str, str] = {}
    for name, y0f, y1f in OCR_BANDS:
        y0, y1 = int(h * y0f), max(int(h * y1f), int(h * y0f) + 1)
        crop = img.crop((0, y0, w, y1))
        prep = _prep_for_ocr(crop, scale=2.0 if name == 'url_bar' else 1.5)
        cfg = '--psm 7' if name == 'url_bar' else '--psm 6'
        out[name] = pytesseract.image_to_string(prep, config=cfg)
    return out


def normalize_ocr(text: str) -> tuple[str, str]:
    """Return (spaced_lower, compact_lower) for tolerant domain matching."""
    spaced = re.sub(r'\s+', ' ', text.lower()).strip()
    compact = re.sub(r'[^a-z0-9]+', '', spaced)
    return spaced, compact


def classify_text(text: str) -> tuple[list[str], list[str]]:
    ai_labels: list[str] = []
    research: list[str] = []
    spaced, compact = normalize_ocr(text)

    for pat, label in AI_DOMAIN_PATTERNS:
        if pat.search(spaced) or pat.search(compact):
            ai_labels.append(label)
    for pat, label in AI_UI_PATTERNS:
        if pat.search(spaced):
            ai_labels.append(label)

    if RESEARCH_HINTS.search(spaced) and not ai_labels:
        research.append('research_hint')
    return sorted(set(ai_labels)), research


def classify_frame(ocr_by_band: dict[str, str]) -> tuple[list[str], str]:
    combined = '\n'.join(ocr_by_band.values())
    labels, _ = classify_text(combined)
    excerpt = combined.strip().replace('\n', ' ')[:240]
    return labels, excerpt


@dataclass
class FrameHit:
    offset_sec: float
    window_start_sec: float | None
    ai_labels: list[str]
    ocr_excerpt: str
    ocr_bands_hit: list[str] = field(default_factory=list)


@dataclass
class VideoAIReport:
    session_id: str
    session_dir: str
    used_ai_web: bool = False
    confidence: str = 'none'
    web_ai_windows_hit: int = 0
    web_ai_frames_hit: int = 0
    ai_domains_seen: list[str] = field(default_factory=list)
    frame_hits: list[dict] = field(default_factory=list)
    layer1: dict | None = None
    merged_used_ai: bool = False
    merged_confidence: str = 'none'
    merged_ai_active_sec_est: float = 0.0
    notes: list[str] = field(default_factory=list)


def _estimate_web_ai_duration_v2(layer1: dict, frame_hits: list[dict]) -> float:
    if not frame_hits:
        return 0.0
    hit_windows = {h.get('window_start_sec') for h in frame_hits}
    total = 0.0
    for w in layer1.get('browser_review_windows') or []:
        if w.get('offset_start_sec') in hit_windows:
            total += float(w.get('duration_sec') or 0)
    return round(total, 1)


def analyze_session(session_dir: Path, sample_every: float, work_dir: Path | None) -> VideoAIReport:
    session_dir = session_dir.expanduser().resolve()
    video = session_dir / 'recording.mp4'
    if not video.is_file():
        raise SystemExit(f'missing {video}')

    layer1 = load_json(session_dir / 'ai_usage_report.json')
    if layer1 is None:
        analysis_dir = Path(__file__).resolve().parent
        sys.path.insert(0, str(analysis_dir))
        from detect_ai_usage import analyze_session as layer1_analyze  # type: ignore
        layer1_obj = layer1_analyze(session_dir)
        layer1 = asdict(layer1_obj)
        (session_dir / 'ai_usage_report.json').write_text(
            json.dumps(layer1, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    sid = layer1.get('session_id') or session_dir.name
    report = VideoAIReport(session_id=sid, session_dir=str(session_dir), layer1=layer1)

    windows = layer1.get('browser_review_windows') or []
    if not windows:
        report.notes.append('no browser_review_windows — nothing to OCR')
        _merge_layer1_and_video(report)
        return report

    video_base, base_src = resolve_time_base(session_dir)
    sess_start = session_start_ts(session_dir)
    align_shift = 0.0
    if sess_start is not None:
        align_shift = sess_start - video_base
        if abs(align_shift) > 2:
            report.notes.append(
                f'video anchor {base_src} differs from session_start by {align_shift:.1f}s'
            )

    duration = video_duration_sec(video)
    tmp_root = work_dir or Path(tempfile.mkdtemp(prefix='ai_video_'))
    tmp_root.mkdir(parents=True, exist_ok=True)

    all_domains: set[str] = set()
    windows_with_hits = 0

    for win in windows:
        win_hits: list[FrameHit] = []
        off_start = win.get('offset_start_sec')
        off_end = win.get('offset_end_sec')
        if off_start is None or off_end is None:
            continue
        vid_start = off_start + align_shift
        vid_end = min(duration - 0.5, off_end + align_shift)
        if vid_end <= 0 or vid_start >= duration:
            continue

        for pt in sample_points(vid_start, vid_end, sample_every):
            jpg = tmp_root / f'frame_{sid}_{int(pt*1000)}.jpg'
            if not extract_frame(video, pt, jpg):
                continue
            ocr_by_band = ocr_bands(jpg)
            ai_labels, excerpt = classify_frame(ocr_by_band)
            if ai_labels:
                bands_hit = [
                    b for b, t in ocr_by_band.items()
                    if classify_text(t)[0]
                ]
                hit = FrameHit(
                    offset_sec=round(pt, 1),
                    window_start_sec=off_start,
                    ai_labels=ai_labels,
                    ocr_excerpt=excerpt,
                    ocr_bands_hit=bands_hit,
                )
                win_hits.append(hit)
                all_domains.update(ai_labels)

        if win_hits:
            windows_with_hits += 1
            for h in win_hits:
                report.frame_hits.append(asdict(h))

    report.web_ai_windows_hit = windows_with_hits
    report.web_ai_frames_hit = len(report.frame_hits)
    report.ai_domains_seen = sorted(all_domains)
    report.used_ai_web = bool(all_domains)

    if report.web_ai_frames_hit >= 2:
        report.confidence = 'high'
    elif report.web_ai_frames_hit >= 1:
        report.confidence = 'medium'
    else:
        report.confidence = 'low' if windows else 'none'
        if windows:
            report.notes.append('browser windows present but no AI OCR hits')

    _merge_layer1_and_video(report)
    return report


def _merge_layer1_and_video(report: VideoAIReport) -> None:
    l1 = report.layer1 or {}
    native_sec = float(l1.get('ai_active_sec') or 0)
    native = bool(l1.get('used_ai_native'))
    web = report.used_ai_web

    report.merged_used_ai = native or web
    web_est = _estimate_web_ai_duration_v2(l1, report.frame_hits)

    if native and native_sec >= 30:
        report.merged_confidence = 'high'
        report.merged_ai_active_sec_est = native_sec
    elif web and report.confidence in ('high', 'medium'):
        report.merged_confidence = 'high' if report.confidence == 'high' else 'medium'
        report.merged_ai_active_sec_est = max(native_sec, web_est)
    elif native:
        report.merged_confidence = 'medium'
        report.merged_ai_active_sec_est = native_sec
    elif web:
        report.merged_confidence = report.confidence
        report.merged_ai_active_sec_est = web_est
    elif l1.get('needs_video_review'):
        report.merged_confidence = 'inconclusive'
        report.merged_ai_active_sec_est = 0.0
        report.notes.append('browser time present; video OCR found no AI UI')
    else:
        report.merged_confidence = l1.get('confidence', 'none')
        report.merged_ai_active_sec_est = native_sec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dir', type=Path)
    ap.add_argument('--sample-every', type=float, default=15.0,
                    help='Seconds between frame samples inside each browser window')
    ap.add_argument('--work-dir', type=Path, default=None,
                    help='Keep extracted frames here (default: temp dir)')
    ap.add_argument('--write-json', action='store_true',
                    help='Write ai_video_report.json into session dir')
    args = ap.parse_args()

    if not shutil.which('ffmpeg'):
        print('ERROR: ffmpeg not found', file=sys.stderr)
        sys.exit(1)
    if not shutil.which('tesseract'):
        print('ERROR: tesseract not found', file=sys.stderr)
        sys.exit(1)

    report = analyze_session(args.session_dir, args.sample_every, args.work_dir)
    out = {
        'session_id': report.session_id,
        'used_ai_web': report.used_ai_web,
        'confidence': report.confidence,
        'web_ai_windows_hit': report.web_ai_windows_hit,
        'web_ai_frames_hit': report.web_ai_frames_hit,
        'ai_domains_seen': report.ai_domains_seen,
        'merged_used_ai': report.merged_used_ai,
        'merged_confidence': report.merged_confidence,
        'merged_ai_active_sec_est': report.merged_ai_active_sec_est,
        'frame_hits': report.frame_hits,
        'layer1': report.layer1,
        'notes': report.notes,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))

    if args.write_json:
        path = args.session_dir / 'ai_video_report.json'
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'Wrote {path}', file=sys.stderr)


if __name__ == '__main__':
    main()
