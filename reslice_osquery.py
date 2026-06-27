#!/usr/bin/env python3
"""
reslice_osquery.py - recover osquery.jsonl (and an orphaned recording)
for a session whose cleanup trap did not finish.

Reads the session's time window from session_metadata.json, slices the
osquery global log into <session_dir>/osquery.jsonl, and - if recording.mp4
is missing - looks for an un-moved OBS recording and moves it in.

IMPORTANT: osquery events expire from the global log after events_expiry
(default 3600s = 1 hour). Run this within an hour of the session, or the
events will already be gone and the slice will be empty.

Usage:
    python reslice_osquery.py $THESIS_DIR/sessions/<SESSION_ID>
"""

import os
import json
import sys
import datetime as dt
from pathlib import Path

THESIS_DIR = Path(os.environ.get("THESIS_DIR", Path(__file__).resolve().parent))
OSQUERY_LOG = THESIS_DIR / 'osquery_logs' / 'osqueryd.results.log'
RECORDINGS_DIR = THESIS_DIR / 'recordings'


def parse_iso_to_unix(iso_str):
    """Convert an ISO-8601 UTC string to a Unix timestamp (int seconds)."""
    # Handles trailing +00:00 and fractional seconds.
    d = dt.datetime.fromisoformat(iso_str)
    return int(d.timestamp())


def reslice(session_dir):
    session_dir = Path(session_dir)
    meta_path = session_dir / 'session_metadata.json'

    if not session_dir.is_dir():
        print(f'ERROR: session dir not found: {session_dir}')
        return 1
    if not meta_path.is_file():
        print(f'ERROR: session_metadata.json not found in {session_dir}')
        return 1

    meta = json.loads(meta_path.read_text())
    start_iso = meta.get('session_start_utc')
    end_iso = meta.get('session_end_utc')

    if not start_iso:
        print('ERROR: session_start_utc missing from metadata')
        return 1
    if not end_iso:
        # If the session never recorded an end (hard crash), use start + planned duration.
        planned = meta.get('duration_planned_sec', 1560)
        start_u = parse_iso_to_unix(start_iso)
        end_u = start_u + int(planned) + 10
        print(f'WARNING: session_end_utc missing; using start + planned ({planned}s)')
    else:
        start_u = parse_iso_to_unix(start_iso)
        end_u = parse_iso_to_unix(end_iso)

    print(f'Session window (unix): {start_u} - {end_u}  ({end_u - start_u}s)')

    # ---- osquery slice ----
    out_path = session_dir / 'osquery.jsonl'
    if out_path.is_file() and out_path.stat().st_size > 0:
        print(f'osquery.jsonl already present and non-empty ({out_path.stat().st_size} B). '
              f'Skipping slice. Delete it first if you want to re-slice.')
    elif not OSQUERY_LOG.is_file():
        print(f'ERROR: osquery global log not found at {OSQUERY_LOG}')
    else:
        kept = 0
        scanned = 0
        with open(OSQUERY_LOG, encoding='utf-8', errors='replace') as fin, \
                open(out_path, 'w') as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                scanned += 1
                try:
                    r = json.loads(line)
                    ut = int(r.get('unixTime', 0))
                    if start_u <= ut <= end_u:
                        fout.write(line + '\n')
                        kept += 1
                except Exception:
                    pass
        print(f'osquery: scanned {scanned} log lines, kept {kept} in window '
              f'-> {out_path}')
        if kept == 0:
            print('  NOTE: 0 events kept. Either no user activity in the window, '
                  'or events already expired from the global log (events_expiry).')

    # ---- orphaned recording recovery ----
    rec_path = session_dir / 'recording.mp4'
    if rec_path.is_file() and rec_path.stat().st_size > 0:
        print(f'recording.mp4 already present ({rec_path.stat().st_size} B). Skipping.')
    elif not RECORDINGS_DIR.is_dir():
        print(f'No recordings folder at {RECORDINGS_DIR}; cannot recover recording.')
    else:
        # Find OBS recordings whose mtime falls in or just after the session window.
        candidates = []
        for mp4 in RECORDINGS_DIR.glob('*.mp4'):
            mtime = int(mp4.stat().st_mtime)
            # OBS finalizes the file at recording stop, so its mtime is around end_u.
            if start_u <= mtime <= end_u + 120:
                candidates.append((mtime, mp4))
        if not candidates:
            print('No orphaned recording found matching this session window. '
                  '(If the recording was already moved or never made, this is expected.)')
        elif len(candidates) > 1:
            print('Multiple candidate recordings found in the window; not moving '
                  'automatically to avoid picking the wrong one:')
            for mtime, mp4 in sorted(candidates):
                print(f'  {dt.datetime.utcfromtimestamp(mtime)}Z  {mp4}')
            print('Move the correct one manually:')
            print(f'  mv "<chosen>.mp4" "{rec_path}"')
        else:
            _, mp4 = candidates[0]
            mp4.rename(rec_path)
            print(f'Moved orphaned recording into session dir:\n  {mp4}\n  -> {rec_path}')

    print('Done.')
    return 0


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    return reslice(sys.argv[1])


if __name__ == '__main__':
    sys.exit(main())
