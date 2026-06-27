"""
slice_safari_history.py
=========================

Extract Safari history visits that fall within each session time window from the
shared Safari-History.db and write per-session artifacts.

USAGE
-----
    python slice_safari_history.py SESSION_DIR
    python slice_safari_history.py --all
    python slice_safari_history.py --all --safari-db /path/to/Safari-History.db

OUTPUT (per session)
  safari_history.jsonl       one JSON object per visit
  safari_history_summary.json  aggregate counts and AI domain summary
"""

from __future__ import annotations
import os

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Safari visit_time is CFAbsoluteTime (seconds since 2001-01-01 UTC).
CF_ABSOLUTE_EPOCH_OFFSET = 978307200

AI_DOMAIN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), label)
    for p, label in [
        (r'chatgpt\.com', 'chatgpt'),
        (r'chat\.openai', 'chatgpt'),
        (r'openai\.com/chat', 'chatgpt'),
        (r'claude\.ai', 'claude'),
        (r'anthropic\.com', 'anthropic'),
        (r'gemini\.google', 'gemini'),
        (r'copilot\.microsoft', 'copilot'),
        (r'bing\.com/chat', 'copilot'),
        (r'perplexity\.ai', 'perplexity'),
        (r'cursor\.com', 'cursor'),
        (r'poe\.com', 'poe'),
        (r'you\.com', 'you'),
        (r'phind\.com', 'phind'),
        (r'mistral\.ai', 'mistral'),
        (r'character\.ai', 'character'),
    ]
]

DEFAULT_SAFARI_DB = DEFAULT_SESSIONS_ROOT / 'Safari-History.db'
DEFAULT_SESSIONS_ROOT = Path(os.environ.get("THESIS_DIR", Path(__file__).resolve().parent.parent)) / 'sessions'
FALLBACK_OUTPUT_ROOT = Path(__file__).resolve().parent / 'output'


def parse_ts(s: str) -> float:
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    return datetime.fromisoformat(s).timestamp()


def iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def extract_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc or ''
    except Exception:
        host = ''
    return host.lower().removeprefix('www.')


def classify_ai(url: str, domain: str) -> str | None:
    hay = f'{url} {domain}'.lower()
    for pat, label in AI_DOMAIN_PATTERNS:
        if pat.search(hay):
            return label
    return None


def load_session_window(session_dir: Path) -> tuple[float | None, float | None, str | None]:
    meta_path = session_dir / 'session_metadata.json'
    sid = session_dir.name
    t0: float | None = None
    t1: float | None = None

    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        sid = meta.get('session_id') or sid
        if meta.get('session_start_utc'):
            t0 = parse_ts(meta['session_start_utc'])
        if meta.get('session_end_utc'):
            t1 = parse_ts(meta['session_end_utc'])

    if t0 is None and (session_dir / 'recording_start.txt').is_file():
        t0 = parse_ts((session_dir / 'recording_start.txt').read_text(encoding='utf-8'))

    if t1 is None:
        t1 = _focused_app_end_ts(session_dir)
    return t0, t1, sid


def _focused_app_end_ts(session_dir: Path) -> float | None:
    path = session_dir / 'focused_app.jsonl'
    if not path.is_file():
        return None
    last_ts: float | None = None
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get('timestamp_utc'):
            last_ts = parse_ts(r['timestamp_utc'])
    return last_ts


def discover_sessions(sessions_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(sessions_root.iterdir()):
        if not p.is_dir() or not p.name.startswith('P'):
            continue
        if (p / 'session_metadata.json').is_file() or (p / 'recording_start.txt').is_file():
            out.append(p)
    return out


def cf_to_unix(visit_time: float) -> float:
    return visit_time + CF_ABSOLUTE_EPOCH_OFFSET


def fetch_visits(db_path: Path, t0: float, t1: float) -> list[dict]:
    cf0 = t0 - CF_ABSOLUTE_EPOCH_OFFSET
    cf1 = t1 - CF_ABSOLUTE_EPOCH_OFFSET
    conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            '''
            SELECT v.visit_time, i.url, v.title
            FROM history_visits v
            JOIN history_items i ON v.history_item = i.id
            WHERE v.visit_time >= ? AND v.visit_time <= ?
            ORDER BY v.visit_time ASC
            ''',
            (cf0, cf1),
        ).fetchall()
    finally:
        conn.close()

    visits: list[dict] = []
    for row in rows:
        ts = cf_to_unix(float(row['visit_time']))
        url = row['url'] or ''
        domain = extract_domain(url)
        ai_label = classify_ai(url, domain)
        visits.append({
            'timestamp_utc': iso_utc(ts),
            'timestamp_unix': round(ts, 3),
            'url': url,
            'title': row['title'] or '',
            'domain': domain,
            'ai_label': ai_label,
        })
    return visits


def build_summary(session_id: str, t0: float | None, t1: float | None,
                  visits: list[dict]) -> dict:
    ai_visits = [v for v in visits if v.get('ai_label')]
    ai_domains = sorted({v['ai_label'] for v in ai_visits if v.get('ai_label')})
    first_ai_ts = ai_visits[0]['timestamp_utc'] if ai_visits else None
    last_ai_ts = ai_visits[-1]['timestamp_utc'] if ai_visits else None

    span_sec = 0.0
    if len(ai_visits) >= 2:
        span_sec = ai_visits[-1]['timestamp_unix'] - ai_visits[0]['timestamp_unix']
    elif len(ai_visits) == 1:
        span_sec = 0.0

    return {
        'session_id': session_id,
        'window': {
            'session_start_utc': iso_utc(t0) if t0 is not None else None,
            'session_end_utc': iso_utc(t1) if t1 is not None else None,
        },
        'visit_count': len(visits),
        'ai_visit_count': len(ai_visits),
        'ai_domains': ai_domains,
        'first_ai_ts': first_ai_ts,
        'last_ai_ts': last_ai_ts,
        'estimated_span_sec': round(span_sec, 1),
    }


def slice_session(session_dir: Path, safari_db: Path) -> dict:
    session_dir = session_dir.expanduser().resolve()
    t0, t1, sid = load_session_window(session_dir)
    sid = sid or session_dir.name

    summary = build_summary(sid, t0, t1, [])
    summary['notes'] = []

    if t0 is None:
        summary['notes'].append('no session window (missing session_metadata.json / recording_start.txt)')
        return summary
    if t1 is None:
        summary['notes'].append('session_end_utc missing — cannot slice history window')
        return summary
    if not safari_db.is_file():
        summary['notes'].append(f'safari db missing: {safari_db}')
        return summary

    visits = fetch_visits(safari_db, t0, t1)
    summary = build_summary(sid, t0, t1, visits)
    summary['notes'] = []
    if not visits:
        summary['notes'].append('no safari visits in session window')

    summary['files_written'] = []
    jsonl_path = session_dir / 'safari_history.jsonl'
    summary_path = session_dir / 'safari_history_summary.json'
    try:
        with jsonl_path.open('w', encoding='utf-8') as f:
            for v in visits:
                f.write(json.dumps(v, ensure_ascii=False) + '\n')
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n',
                                encoding='utf-8')
        summary['files_written'] = [str(jsonl_path), str(summary_path)]
    except OSError as exc:
        fallback_dir = FALLBACK_OUTPUT_ROOT / session_dir.name
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fb_jsonl = fallback_dir / 'safari_history.jsonl'
        fb_summary = fallback_dir / 'safari_history_summary.json'
        with fb_jsonl.open('w', encoding='utf-8') as f:
            for v in visits:
                f.write(json.dumps(v, ensure_ascii=False) + '\n')
        fb_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n',
                              encoding='utf-8')
        summary['notes'].append(f'session dir write failed ({exc}); wrote fallback under {fallback_dir}')
        summary['files_written'] = [str(fb_jsonl), str(fb_summary)]
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dirs', nargs='*', help='Session directory path(s)')
    ap.add_argument('--all', action='store_true',
                    help='Process every session under --sessions-root')
    ap.add_argument('--sessions-root', type=Path, default=DEFAULT_SESSIONS_ROOT)
    ap.add_argument('--safari-db', type=Path, default=DEFAULT_SAFARI_DB)
    args = ap.parse_args()

    targets = [Path(p).expanduser() for p in args.session_dirs]
    if args.all:
        targets = discover_sessions(args.sessions_root.expanduser())

    if not targets:
        ap.error('Provide SESSION_DIR(s) or use --all')

    for session_dir in targets:
        if not session_dir.is_dir():
            print(f'ERROR: not a directory: {session_dir}', file=sys.stderr)
            sys.exit(1)
        summary = slice_session(session_dir, args.safari_db.expanduser())
        print(f"=== {summary['session_id']} ===")
        print(f"  visits: {summary.get('visit_count', 0)}  ai_visits: {summary.get('ai_visit_count', 0)}")
        if summary.get('ai_domains'):
            print(f"  ai_domains: {', '.join(summary['ai_domains'])}")
        for note in summary.get('notes') or []:
            print(f'  note: {note}')
        for path in summary.get('files_written') or []:
            print(f'  -> {path}')
        print()


if __name__ == '__main__':
    main()
