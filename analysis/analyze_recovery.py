"""
analyze_recovery.py
===================

Baseline -> task -> post-send recovery arc, using the actual email-send event.

The participant's sent recommendation email (email-*.eml in the session folder)
carries a Date header. That is a true task-completion boundary, unlike a typing
pause. This script splits each session into three phases relative to that event:

    baseline   first BASELINE_SEC seconds (proxy low-demand state)
    task       baseline end -> email sent
    post-send  email sent -> end of recording (only if it falls in-session)

and reports mean heart rate and RMSSD in each, so the recovery arc is visible.
It uses raw stream extents (not the trimmed common-window grid) so the post-send
tail is not cut off.

USAGE
-----
    python analyze_recovery.py SESSION_DIR [SESSION_DIR ...]
"""

import os
import sys
import json
import glob
import email
import numpy as np
from email.utils import parsedate_to_datetime
from datetime import datetime

BASELINE_SEC = 90
POSTTASK_MIN_SEC = 60


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def phase(hr, rr, a, b):
    h = [hr[s] for s in range(int(a), int(b)) if s in hr]
    xs = []
    for s in range(int(a), int(b)):
        xs.extend(rr.get(s, []))
    rm = np.sqrt(np.mean(np.diff(xs) ** 2)) if len(xs) >= 5 else float('nan')
    return (np.mean(h) if h else float('nan'), rm)


def main():
    dirs = sys.argv[1:]
    if not dirs:
        print(__doc__)
        sys.exit(1)

    print(f'{"Session":8s} {"sent@":>7s} {"end":>6s} {"post(s)":>8s}  '
          f'{"HR base/task/post":>22s}   {"RMSSD base/task/post":>22s}')
    print('-' * 96)
    for d in dirs:
        label = os.path.basename(d.rstrip('/')).split('_')[0]
        pol = [json.loads(l) for l in open(os.path.join(d, 'polar.jsonl'))]
        inp = [json.loads(l) for l in open(os.path.join(d, 'input.jsonl'))]
        t0 = min([ts(r['timestamp_utc']) for r in inp] +
                 [ts(r['timestamp_utc']) for r in pol])
        tend = max(ts(r['timestamp_utc']) for r in inp) - t0
        eml = (glob.glob(os.path.join(d, 'email-*.eml'))
               or glob.glob(os.path.join(d, '*email*.eml')))
        if not eml:
            print(f'{label:8s} (no email .eml)')
            continue
        msg = email.message_from_file(open(eml[0]))
        sent = parsedate_to_datetime(msg.get('Date')).timestamp() - t0

        hr, rr = {}, {}
        for r in pol:
            s = int(ts(r['timestamp_utc']) - t0)
            hr.setdefault(s, []).append(r['heart_rate_bpm'])
            for x in r.get('rr_intervals_ms', []):
                rr.setdefault(s, []).append(x)
        hr = {k: np.mean(v) for k, v in hr.items()}

        bl = phase(hr, rr, 0, BASELINE_SEC)
        post_len = tend - sent
        if post_len < POSTTASK_MIN_SEC:
            note = (f'sent after recording' if sent > tend
                    else f'window {post_len:.0f}s < {POSTTASK_MIN_SEC}s')
            print(f'{label:8s} {sent:7.0f} {tend:6.0f} {"--":>8s}  '
                  f'(no in-session post-send window: {note})')
            continue
        task = phase(hr, rr, BASELINE_SEC, sent)
        post = phase(hr, rr, sent, tend)
        print(f'{label:8s} {sent:7.0f} {tend:6.0f} {post_len:8.0f}  '
              f'{bl[0]:5.1f}/{task[0]:5.1f}/{post[0]:5.1f}   '
              f'{bl[1]:6.1f}/{task[1]:6.1f}/{post[1]:6.1f}')

    print('\nA recovery arc = HR rises baseline->task then falls task->post-send,')
    print('with RMSSD moving the opposite way. Exploratory; post-task state is')
    print('not experimentally controlled.')


if __name__ == '__main__':
    main()
