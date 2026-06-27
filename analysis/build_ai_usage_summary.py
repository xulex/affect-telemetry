"""
build_ai_usage_summary.py
=========================

Merge layer-1 AI usage, sliced Safari history, and video OCR reports into a
single per-session analysis file and print a recheck table for all sessions.

USAGE
-----
    python build_ai_usage_summary.py --all
    python build_ai_usage_summary.py SESSION_DIR [SESSION_DIR ...]
    python build_ai_usage_summary.py --all --slice-safari
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from detect_ai_usage import analyze_session as analyze_layer1

DEFAULT_SESSIONS_ROOT = Path('/Users/Shared/thesis-phase1/sessions')
DEFAULT_SAFARI_DB = Path('/Users/Shared/thesis-phase1/sessions/Safari-History.db')
FALLBACK_OUTPUT_ROOT = Path(__file__).resolve().parent / 'output'

BROWSER_SIGNAL_SEC = 60
SAFARI_AI_VISIT_THRESHOLD = 1


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def discover_sessions(sessions_root: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(sessions_root.iterdir()):
        if not p.is_dir() or not p.name.startswith('P'):
            continue
        if (p / 'session_metadata.json').is_file():
            out.append(p)
    return out


def ensure_layer1(session_dir: Path) -> dict:
    path = session_dir / 'ai_usage_report.json'
    data = load_json(path)
    if data is not None:
        return data
    report = analyze_layer1(session_dir)
    data = asdict(report)
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    except OSError:
        pass
    return data


def load_safari_summary(session_dir: Path, safari_db: Path | None = None) -> dict | None:
    data = load_json(session_dir / 'safari_history_summary.json')
    if data is not None:
        return data
    if safari_db is None:
        return None
    from slice_safari_history import slice_session
    return slice_session(session_dir, safari_db)


def safari_has_ai_signal(safari: dict | None) -> bool:
    if not safari:
        return False
    return (safari.get('ai_visit_count') or 0) >= SAFARI_AI_VISIT_THRESHOLD


def video_has_ai_signal(video: dict | None) -> bool:
    if not video:
        return False
    return bool(video.get('used_ai_web'))


def native_has_ai_signal(layer1: dict) -> bool:
    return bool(layer1.get('used_ai_native'))


def significant_browser_signal(layer1: dict, safari: dict | None) -> bool:
    if safari_has_ai_signal(safari):
        return True
    return float(layer1.get('browser_ambiguous_sec') or 0) >= BROWSER_SIGNAL_SEC


def collect_ai_tools(layer1: dict, safari: dict | None, video: dict | None) -> list[str]:
    tools: set[str] = set()
    for app in (layer1.get('native_apps') or {}):
        tools.add(app.lower())
    for app in layer1.get('osquery_native_apps') or []:
        tools.add(str(app).lower())
    for label in (safari or {}).get('ai_domains') or []:
        tools.add(label)
    for label in (video or {}).get('ai_domains_seen') or []:
        tools.add(label)
    return sorted(tools)


def estimate_active_sec(layer1: dict, safari: dict | None, video: dict | None) -> float:
    native_sec = float(layer1.get('ai_active_sec') or 0)
    video_sec = float((video or {}).get('merged_ai_active_sec_est') or 0)
    safari_span = float((safari or {}).get('estimated_span_sec') or 0)
    safari_visits = int((safari or {}).get('ai_visit_count') or 0)
    safari_est = safari_span if safari_visits >= 2 else min(30.0 * safari_visits, safari_span + 15.0)
    return round(max(native_sec, video_sec, safari_est if safari_has_ai_signal(safari) else 0), 1)


def build_final_recommendation(layer1: dict, safari: dict | None,
                               video: dict | None) -> dict:
    safari_ai = safari_has_ai_signal(safari)
    video_ai = video_has_ai_signal(video)
    native_ai = native_has_ai_signal(layer1)

    evidence: list[str] = []
    if native_ai:
        evidence.append('native_app')
    if safari_ai:
        evidence.append('safari_history')
    if video_ai:
        evidence.append('video_ocr')
    if float(layer1.get('browser_ambiguous_sec') or 0) >= BROWSER_SIGNAL_SEC:
        evidence.append('browser_focus')

    used_ai = native_ai or safari_ai or video_ai

    if native_ai and float(layer1.get('ai_active_sec') or 0) >= 30:
        confidence = 'high'
    elif video_ai and (video or {}).get('confidence') == 'high':
        confidence = 'high'
    elif native_ai or (video_ai and (video or {}).get('confidence') in ('high', 'medium')):
        confidence = 'high' if (native_ai and safari_ai) or (video or {}).get('confidence') == 'high' else 'medium'
    elif safari_ai and (safari or {}).get('ai_visit_count', 0) >= 2:
        confidence = 'medium'
    elif safari_ai:
        confidence = 'low'
    elif used_ai:
        confidence = 'medium'
    elif significant_browser_signal(layer1, safari):
        confidence = 'low'
    else:
        confidence = 'none'

    if video and not video_ai and safari_ai and (video.get('confidence') in ('low', 'none')):
        confidence = 'low'

    return {
        'used_ai': used_ai,
        'confidence': confidence,
        'ai_tools': collect_ai_tools(layer1, safari, video),
        'ai_active_sec_est': estimate_active_sec(layer1, safari, video),
        'evidence_sources': evidence,
    }


def compute_needs_video_review(layer1: dict, safari: dict | None,
                               video: dict | None, final: dict) -> bool:
    if not session_dir_has_recording(layer1):
        return False

    if native_high_confidence(layer1):
        return False

    browser_signal = significant_browser_signal(layer1, safari)
    safari_ai = safari_has_ai_signal(safari)

    if safari_ai and video is None:
        return True

    if browser_signal and video is None and not native_has_ai_signal(layer1):
        return True

    if video is not None:
        vid_conf = video.get('confidence', 'none')
        merged_conf = video.get('merged_confidence', 'none')
        if safari_ai and not video_has_ai_signal(video):
            return True
        if browser_signal and vid_conf in ('low', 'none') and merged_conf == 'inconclusive':
            return True
        if layer1.get('needs_video_review') and not video_has_ai_signal(video) and browser_signal:
            return True

    if layer1.get('needs_video_review') and video is None:
        return True

    return False


def session_dir_has_recording(layer1: dict) -> bool:
    return bool(layer1.get('recording_available'))


def history_video_disagree(safari: dict | None, video: dict | None) -> str | None:
    if safari is None or video is None:
        return None
    notes = safari.get('notes') or []
    if any('cannot slice' in n or 'missing' in n for n in notes):
        return None
    if (safari.get('visit_count') or 0) == 0 and not safari.get('window', {}).get('session_end_utc'):
        return None
    s = safari_has_ai_signal(safari)
    v = video_has_ai_signal(video)
    if s and not v:
        return 'safari_ai_yes_video_no'
    if v and not s:
        return 'video_ai_yes_safari_no'
    return None


def native_high_confidence(layer1: dict) -> bool:
    return native_has_ai_signal(layer1) and layer1.get('confidence') == 'high'


def build_summary(session_dir: Path, safari_db: Path | None = None) -> dict:
    session_dir = session_dir.expanduser().resolve()
    layer1 = ensure_layer1(session_dir)
    safari = load_safari_summary(session_dir, safari_db=safari_db)
    video = load_json(session_dir / 'ai_video_report.json')

    window = {
        'session_start_utc': layer1.get('session_start_utc'),
        'session_end_utc': layer1.get('session_end_utc'),
    }

    final = build_final_recommendation(layer1, safari, video)
    needs_video = compute_needs_video_review(layer1, safari, video, final)
    disagree = history_video_disagree(safari, video)

    summary = {
        'session_id': layer1.get('session_id') or session_dir.name,
        'session_dir': str(session_dir),
        'window': window,
        'layer1': {
            'used_ai_native': layer1.get('used_ai_native'),
            'confidence': layer1.get('confidence'),
            'ai_active_sec': layer1.get('ai_active_sec'),
            'browser_ambiguous_sec': layer1.get('browser_ambiguous_sec'),
            'needs_video_review': layer1.get('needs_video_review'),
            'native_apps': layer1.get('native_apps'),
            'browser_apps': layer1.get('browser_apps'),
            'osquery_native_apps': layer1.get('osquery_native_apps'),
            'recording_available': layer1.get('recording_available'),
        },
        'safari_history': safari,
        'video': None if video is None else {
            'used_ai_web': video.get('used_ai_web'),
            'confidence': video.get('confidence'),
            'merged_used_ai': video.get('merged_used_ai'),
            'merged_confidence': video.get('merged_confidence'),
            'merged_ai_active_sec_est': video.get('merged_ai_active_sec_est'),
            'ai_domains_seen': video.get('ai_domains_seen'),
            'web_ai_frames_hit': video.get('web_ai_frames_hit'),
        },
        'final_recommendation': final,
        'needs_video_review': needs_video,
        'history_video_disagreement': disagree,
    }
    return summary


def write_summary(session_dir: Path, summary: dict) -> Path | None:
    out = session_dir / 'ai_usage_summary.json'
    try:
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        return out
    except OSError as exc:
        fallback = FALLBACK_OUTPUT_ROOT / session_dir.name / 'ai_usage_summary.json'
        fallback.parent.mkdir(parents=True, exist_ok=True)
        summary.setdefault('notes', []).append(f'session dir write failed ({exc}); wrote fallback')
        fallback.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        return fallback


def print_recheck_table(summaries: list[dict]) -> None:
    headers = (
        'session', 'safari_ai', 'video_ai', 'native_ai',
        'final_used_ai', 'confidence', 'needs_more_video',
    )
    rows: list[tuple[str, ...]] = []
    for s in summaries:
        sid = s['session_id']
        safari_ai = safari_has_ai_signal(s.get('safari_history'))
        video = s.get('video') or {}
        video_ai = bool(video.get('used_ai_web'))
        native_ai = bool(s.get('layer1', {}).get('used_ai_native'))
        final = s.get('final_recommendation') or {}
        rows.append((
            sid,
            str(safari_ai),
            str(video_ai) if s.get('video') is not None else 'n/a',
            str(native_ai),
            str(final.get('used_ai')),
            str(final.get('confidence')),
            str(s.get('needs_video_review')),
        ))

    widths = [max(len(h), max(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    fmt = ' | '.join(f'{{:{w}}}' for w in widths)
    print(fmt.format(*headers))
    print('-+-'.join('-' * w for w in widths))
    for row in rows:
        print(fmt.format(*row))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dirs', nargs='*', help='Session directory path(s)')
    ap.add_argument('--all', action='store_true',
                    help='Process every session with session_metadata.json')
    ap.add_argument('--sessions-root', type=Path, default=DEFAULT_SESSIONS_ROOT)
    ap.add_argument('--slice-safari', action='store_true',
                    help='Run slice_safari_history.py before building summaries')
    ap.add_argument('--safari-db', type=Path, default=DEFAULT_SAFARI_DB)
    args = ap.parse_args()

    targets = [Path(p).expanduser() for p in args.session_dirs]
    if args.all:
        targets = discover_sessions(args.sessions_root.expanduser())

    if not targets:
        ap.error('Provide SESSION_DIR(s) or use --all')

    if args.slice_safari:
        from slice_safari_history import slice_session
        for session_dir in targets:
            slice_session(session_dir, args.safari_db.expanduser())

    summaries: list[dict] = []
    files_by_session: dict[str, list[str]] = {}

    for session_dir in targets:
        if not session_dir.is_dir():
            print(f'ERROR: not a directory: {session_dir}', file=sys.stderr)
            sys.exit(1)
        summary = build_summary(session_dir, safari_db=args.safari_db.expanduser())
        out_path = write_summary(session_dir, summary)
        summaries.append(summary)

        written = [str(out_path)] if out_path else []
        safari = summary.get('safari_history') or {}
        for path in safari.get('files_written') or []:
            if path not in written:
                written.append(path)
        if (session_dir / 'safari_history.jsonl').is_file():
            p = str(session_dir / 'safari_history.jsonl')
            if p not in written:
                written.append(p)
        if (session_dir / 'safari_history_summary.json').is_file():
            p = str(session_dir / 'safari_history_summary.json')
            if p not in written:
                written.append(p)
        files_by_session[summary['session_id']] = written

    print('\n=== Files created per session ===')
    for sid, paths in files_by_session.items():
        print(f'{sid}:')
        for p in paths:
            print(f'  {p}')

    print('\n=== Recheck table (all sessions) ===')
    print_recheck_table(summaries)

    needs_video_count = sum(1 for s in summaries if s.get('needs_video_review'))
    print(f'\nSessions needing more video evidence: {needs_video_count}')

    disagreements = [s for s in summaries if s.get('history_video_disagreement')]
    if disagreements:
        print('\nHistory / video disagreements (review these):')
        for s in disagreements:
            print(f"  {s['session_id']}: {s['history_video_disagreement']}")
            sh = s.get('safari_history') or {}
            vd = s.get('video') or {}
            print(f"    safari ai_domains={sh.get('ai_domains')}  "
                  f"video domains={vd.get('ai_domains_seen')}")
    else:
        print('\nNo history/video disagreements among sessions with both signals.')


if __name__ == '__main__':
    main()
