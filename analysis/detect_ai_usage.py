"""
detect_ai_usage.py
==================

Estimate whether a participant used an AI assistant during a session, and for
how long, using existing behavioral streams (no video required for native apps).

Layer 1 (this script)
  - focused_app.jsonl: frontmost-app dwell time
  - osquery.jsonl: native AI app process launches

Layer 2 (flagged only)
  - Sessions with substantial browser time but no native AI app are marked
    needs_video_review=True. Reprocess recording.mp4 separately (URL/OCR) to
    distinguish research browsing from web-based AI.

USAGE
-----
    python detect_ai_usage.py SESSION_DIR
    python detect_ai_usage.py SESSION_DIR [SESSION_DIR ...]
    python detect_ai_usage.py --all
    python detect_ai_usage.py --all --write-json --csv summary.csv

OUTPUT FIELDS (per session)
  used_ai_native      True if a native AI app was frontmost (Claude, ChatGPT, …)
  confidence          high | medium | low | none
  ai_active_sec       Dwell time in native AI apps (high-confidence duration)
  browser_ambiguous_sec  Browser frontmost time (research or web AI — ambiguous)
  needs_video_review  True when browser_ambiguous_sec exceeds threshold and no
                      native AI use was detected
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# Native AI desktop apps (bundle id substring or display name).
NATIVE_AI_BUNDLE_HINTS = (
    'anthropic',
    'claudefordesktop',
    'openai.chat',
    'chatgpt',
    'cursor',
    'todesktop',
    'perplexity',
    'com.google.gemini',
    'copilot',
    'poe',
)
NATIVE_AI_NAME_HINTS = (
    'claude',
    'chatgpt',
    'cursor',
    'perplexity',
    'gemini',
    'copilot',
    'poe',
)

BROWSER_NAMES = frozenset({
    'Safari',
    'Google Chrome',
    'Firefox',
    'Arc',
    'Microsoft Edge',
    'Brave Browser',
    'Opera',
})

# Ignore operator/session tooling when classifying focus time.
IGNORED_FOCUS_NAMES = frozenset({
    'Python',
    'Terminal',
    'iTerm2',
    'Activity Monitor',
    'System Settings',
    'SecurityAgent',
})

BROWSER_REVIEW_THRESHOLD_SEC = 60
NATIVE_AI_HIGH_SEC = 30
NATIVE_AI_MEDIUM_SEC = 5

OSQUERY_SKIP_PARTS = (
    'helper',
    'renderer',
    'crashpad',
    'framework',
    'disclaimer',
)


def parse_ts(s: str) -> float:
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    return datetime.fromisoformat(s).timestamp()


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def is_native_ai_app(bundle_name: str, bundle_id: str) -> bool:
    name = (bundle_name or '').lower()
    bid = (bundle_id or '').lower()
    if any(h in name for h in NATIVE_AI_NAME_HINTS):
        return True
    return any(h in bid for h in NATIVE_AI_BUNDLE_HINTS)


def is_browser_app(bundle_name: str) -> bool:
    return bundle_name in BROWSER_NAMES


def is_osquery_native_path(path: str) -> bool:
    p = path.lower()
    if any(skip in p for skip in OSQUERY_SKIP_PARTS):
        return False
    markers = (
        '/claude.app/',
        '/chatgpt.app/',
        '/cursor.app/',
        '/perplexity.app/',
        '/poe.app/',
        'copilot',
        'gemini',
    )
    return any(m in p for m in markers)


@dataclass
class FocusSegment:
    app: str
    bundle_id: str
    start_ts: float
    end_ts: float
    category: str  # native_ai | browser | other

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)


@dataclass
class AIUsageReport:
    session_id: str
    session_dir: str
    used_ai_native: bool = False
    confidence: str = 'none'
    ai_active_sec: float = 0.0
    browser_ambiguous_sec: float = 0.0
    needs_video_review: bool = False
    native_apps: dict[str, float] = field(default_factory=dict)
    browser_apps: dict[str, float] = field(default_factory=dict)
    osquery_native_apps: list[str] = field(default_factory=list)
    osquery_native_exec_count: int = 0
    browser_review_windows: list[dict] = field(default_factory=list)
    recording_available: bool = False
    session_start_utc: str | None = None
    session_end_utc: str | None = None
    notes: list[str] = field(default_factory=list)


def load_session_window(session_dir: Path) -> tuple[float | None, float | None, str | None]:
    meta_path = session_dir / 'session_metadata.json'
    if not meta_path.is_file():
        return None, None, None
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    t0 = parse_ts(meta['session_start_utc']) if meta.get('session_start_utc') else None
    t1 = parse_ts(meta['session_end_utc']) if meta.get('session_end_utc') else None
    return t0, t1, meta.get('session_id')


def load_focus_segments(session_dir: Path) -> list[FocusSegment]:
    path = session_dir / 'focused_app.jsonl'
    if not path.is_file():
        return []

    records = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        records.append({
            'ts': parse_ts(r['timestamp_utc']),
            'app': r.get('bundle_name') or '',
            'bundle_id': r.get('bundle_identifier') or '',
        })
    if not records:
        return []

    records.sort(key=lambda x: x['ts'])
    t0, t1, _ = load_session_window(session_dir)
    session_end = t1 if t1 is not None else records[-1]['ts']

    segments: list[FocusSegment] = []
    for i, rec in enumerate(records):
        start = rec['ts']
        if i + 1 < len(records):
            end = records[i + 1]['ts']
        else:
            end = session_end

        if t0 is not None:
            start = max(start, t0)
        if t1 is not None:
            end = min(end, t1)
        if end <= start:
            continue

        app = rec['app']
        bundle_id = rec['bundle_id']
        if app in IGNORED_FOCUS_NAMES:
            category = 'ignored'
        elif is_native_ai_app(app, bundle_id):
            category = 'native_ai'
        elif is_browser_app(app):
            category = 'browser'
        else:
            category = 'other'

        segments.append(FocusSegment(
            app=app,
            bundle_id=bundle_id,
            start_ts=start,
            end_ts=end,
            category=category,
        ))
    return segments


def load_osquery_native(session_dir: Path, t0: float | None, t1: float | None) -> tuple[list[str], int]:
    path = session_dir / 'osquery.jsonl'
    if not path.is_file():
        return [], 0

    apps: set[str] = set()
    exec_count = 0
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get('name') != 'process_events_stream':
            continue
        cols = r.get('columns') or {}
        p = cols.get('path') or ''
        if not is_osquery_native_path(p):
            continue
        ts = float(cols.get('time') or r.get('unixTime') or 0)
        if t0 is not None and ts < t0 - 120:
            continue
        if t1 is not None and ts > t1 + 120:
            continue
        if cols.get('event_type') == 'exec':
            exec_count += 1
        # Friendly label from path
        m = re.search(r'/([^/]+)\.app/', p, re.I)
        label = m.group(1) if m else p.split('/')[-1]
        apps.add(label)
    return sorted(apps), exec_count


def analyze_session(session_dir: Path) -> AIUsageReport:
    session_dir = session_dir.expanduser().resolve()
    sid = session_dir.name
    t0, t1, meta_sid = load_session_window(session_dir)
    if meta_sid:
        sid = meta_sid

    report = AIUsageReport(
        session_id=sid,
        session_dir=str(session_dir),
        recording_available=(session_dir / 'recording.mp4').is_file(),
    )
    if t0 is not None:
        report.session_start_utc = iso_utc(t0)
    if t1 is not None:
        report.session_end_utc = iso_utc(t1)

    segments = load_focus_segments(session_dir)
    if not segments and not (session_dir / 'focused_app.jsonl').is_file():
        report.notes.append('focused_app.jsonl missing')
        return report

    native_total = 0.0
    browser_total = 0.0
    browser_windows: list[dict] = []

    for seg in segments:
        d = seg.duration_sec
        if seg.category == 'native_ai':
            native_total += d
            report.native_apps[seg.app] = report.native_apps.get(seg.app, 0.0) + d
        elif seg.category == 'browser':
            browser_total += d
            report.browser_apps[seg.app] = report.browser_apps.get(seg.app, 0.0) + d
            if d >= 10:
                offset_start = (seg.start_ts - t0) if t0 is not None else None
                offset_end = (seg.end_ts - t0) if t0 is not None else None
                browser_windows.append({
                    'app': seg.app,
                    'start_utc': iso_utc(seg.start_ts),
                    'end_utc': iso_utc(seg.end_ts),
                    'duration_sec': round(d, 1),
                    'offset_start_sec': round(offset_start, 1) if offset_start is not None else None,
                    'offset_end_sec': round(offset_end, 1) if offset_end is not None else None,
                })

    osq_apps, osq_exec = load_osquery_native(session_dir, t0, t1)
    report.osquery_native_apps = osq_apps
    report.osquery_native_exec_count = osq_exec

    report.ai_active_sec = round(native_total, 1)
    report.browser_ambiguous_sec = round(browser_total, 1)
    report.used_ai_native = native_total >= NATIVE_AI_MEDIUM_SEC or bool(osq_apps)
    report.browser_review_windows = browser_windows

    if native_total >= NATIVE_AI_HIGH_SEC or (native_total >= NATIVE_AI_MEDIUM_SEC and osq_apps):
        report.confidence = 'high'
    elif native_total > 0 or osq_apps:
        report.confidence = 'medium'
    elif browser_total >= BROWSER_REVIEW_THRESHOLD_SEC:
        report.confidence = 'low'
        report.needs_video_review = True
        report.notes.append(
            f'browser frontmost {browser_total:.0f}s with no native AI app — '
            'web AI possible; review recording or OCR URL bar'
        )
    else:
        report.confidence = 'none'
        if browser_total > 0:
            report.notes.append(f'minor browser time ({browser_total:.0f}s), likely research')

    if report.used_ai_native and report.browser_ambiguous_sec >= BROWSER_REVIEW_THRESHOLD_SEC:
        report.notes.append(
            'native AI detected; extra browser time may include research or web AI'
        )
    elif report.needs_video_review:
        report.notes.append(
            'layer-1 only: web AI not ruled in or out — run process_ai_video.py'
        )

    return report


def format_report(r: AIUsageReport) -> str:
    lines = [
        f'=== {r.session_id} ===',
        f'  used_ai_native:       {r.used_ai_native}',
        f'  confidence:           {r.confidence}',
        f'  ai_active_sec:        {r.ai_active_sec:.1f}  ({r.ai_active_sec / 60:.1f} min)',
        f'  browser_ambiguous_sec:{r.browser_ambiguous_sec:.1f}  ({r.browser_ambiguous_sec / 60:.1f} min)',
        f'  needs_video_review:   {r.needs_video_review}',
        f'  recording_available:  {r.recording_available}',
    ]
    if r.needs_video_review and not r.used_ai_native:
        lines.append('  used_ai_web:          unknown (run process_ai_video.py)')
    if r.native_apps:
        apps = ', '.join(f'{k} {v:.0f}s' for k, v in sorted(r.native_apps.items(), key=lambda x: -x[1]))
        lines.append(f'  native_apps:          {apps}')
    if r.browser_apps:
        apps = ', '.join(f'{k} {v:.0f}s' for k, v in sorted(r.browser_apps.items(), key=lambda x: -x[1]))
        lines.append(f'  browser_apps:         {apps}')
    if r.osquery_native_apps:
        lines.append(f'  osquery_native:       {", ".join(r.osquery_native_apps)} '
                     f'({r.osquery_native_exec_count} exec events)')
    if r.browser_review_windows:
        lines.append(f'  browser_windows:      {len(r.browser_review_windows)} segment(s) for video review')
        for w in r.browser_review_windows[:5]:
            off = ''
            if w.get('offset_start_sec') is not None:
                off = f"  t+{w['offset_start_sec']:.0f}s"
            lines.append(f'    - {w["app"]} {w["duration_sec"]:.0f}s{off}  ({w["start_utc"]} .. {w["end_utc"]})')
        if len(r.browser_review_windows) > 5:
            lines.append(f'    ... and {len(r.browser_review_windows) - 5} more')
    for note in r.notes:
        lines.append(f'  note: {note}')
    return '\n'.join(lines)


def discover_sessions(sessions_root: Path) -> list[Path]:
    out = []
    for p in sorted(sessions_root.iterdir()):
        if p.is_dir() and (p / 'focused_app.jsonl').is_file():
            out.append(p)
    return out


def write_csv(path: Path, reports: list[AIUsageReport]) -> None:
    fields = [
        'session_id', 'used_ai_native', 'confidence', 'ai_active_sec',
        'browser_ambiguous_sec', 'needs_video_review', 'recording_available',
        'native_apps', 'browser_apps', 'osquery_native_apps',
    ]
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in reports:
            w.writerow({
                'session_id': r.session_id,
                'used_ai_native': r.used_ai_native,
                'confidence': r.confidence,
                'ai_active_sec': r.ai_active_sec,
                'browser_ambiguous_sec': r.browser_ambiguous_sec,
                'needs_video_review': r.needs_video_review,
                'recording_available': r.recording_available,
                'native_apps': json.dumps(r.native_apps),
                'browser_apps': json.dumps(r.browser_apps),
                'osquery_native_apps': json.dumps(r.osquery_native_apps),
            })


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dirs', nargs='*', help='Session directory path(s)')
    ap.add_argument('--all', action='store_true',
                    help='Analyze every session under --sessions-root')
    ap.add_argument('--sessions-root', default='/Users/Shared/thesis-phase1/sessions',
                    help='Root folder containing P##_TIMESTAMP session dirs')
    ap.add_argument('--write-json', action='store_true',
                    help='Write ai_usage_report.json into each session dir')
    ap.add_argument('--csv', metavar='PATH',
                    help='Write a summary CSV across analyzed sessions')
    args = ap.parse_args()

    targets: list[Path] = [Path(p).expanduser() for p in args.session_dirs]
    if args.all:
        targets = discover_sessions(Path(args.sessions_root).expanduser())

    if not targets:
        ap.error('Provide SESSION_DIR(s) or use --all')

    reports: list[AIUsageReport] = []
    for session_dir in targets:
        if not session_dir.is_dir():
            print(f'ERROR: not a directory: {session_dir}', file=sys.stderr)
            sys.exit(1)
        report = analyze_session(session_dir)
        reports.append(report)
        print(format_report(report))
        print()
        if args.write_json:
            out = session_dir / 'ai_usage_report.json'
            out.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False) + '\n',
                           encoding='utf-8')
            print(f'  -> wrote {out}\n')

    if args.csv:
        write_csv(Path(args.csv).expanduser(), reports)
        print(f'Wrote CSV summary: {args.csv}')

    # Exit code: 0 always; this is an analysis tool, not a gate.


if __name__ == '__main__':
    main()
