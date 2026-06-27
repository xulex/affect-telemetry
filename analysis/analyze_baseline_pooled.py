"""
analyze_baseline_pooled.py
==========================

Pooled, cross-session baseline-relative analysis of self-reported affect.

Motivation
----------
Pooled ABSOLUTE correlations between physiology and self-report are confounded by
between-person variance: a participant with a higher resting heart rate may also
report higher focus, which inflates the pooled correlation without any
within-person effect. Centering each participant on their own low-demand baseline
removes that offset and leaves the within-person signal.

The consent/name-entry period was never recorded (the live streams start at task
t=0). As a proxy baseline we use the first BASELINE_SEC seconds of each session,
the lowest-demand window available in the captured data. This is a proxy, not a
true pre-task rest baseline, and the result is exploratory at the current sample.

What it does
------------
For each submitted Likert prompt it computes the mean of each physiological
feature in the LIKERT_WINDOW_SEC seconds before the prompt, both in absolute
units and as a deviation from the session's baseline mean. It then pools all
observations and reports, side by side, the absolute and baseline-relative
correlations with the three affect items.

USAGE
-----
    python analyze_baseline_pooled.py SESSION_DIR [SESSION_DIR ...]
"""

import os
import sys
import json
import numpy as np
from datetime import datetime
from scipy import stats

BASELINE_SEC = 90
LIKERT_WINDOW_SEC = 60


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def load_session(d):
    """Return per-second HR dict, per-second RR-interval dict, t0, and prompts."""
    pol = [json.loads(l) for l in open(os.path.join(d, 'polar.jsonl'))]
    inp = [json.loads(l) for l in open(os.path.join(d, 'input.jsonl'))]
    sv = [json.loads(l) for l in open(os.path.join(d, 'survey.jsonl'))]
    t0 = min([ts(r['timestamp_utc']) for r in inp] +
             [ts(r['timestamp_utc']) for r in pol])
    hr, rr = {}, {}
    for r in pol:
        s = int(ts(r['timestamp_utc']) - t0)
        hr.setdefault(s, []).append(r['heart_rate_bpm'])
        for x in r.get('rr_intervals_ms', []):
            rr.setdefault(s, []).append(x)
    hr = {k: np.mean(v) for k, v in hr.items()}
    keys = {}
    for r in inp:
        if r['type'] == 'key_press':
            s = int(ts(r['timestamp_utc']) - t0)
            keys[s] = keys.get(s, 0) + 1
    prompts = [r for r in sv if r.get('type') == 'prompt'
               and r.get('prompt_type') == 'likert'
               and r.get('outcome') == 'submitted']
    return hr, rr, keys, t0, prompts


def win_hr(hr, a, b):
    v = [hr[s] for s in range(int(a), int(b)) if s in hr]
    return np.mean(v) if v else np.nan


def win_rmssd(rr, a, b):
    xs = []
    for s in range(int(a), int(b)):
        xs.extend(rr.get(s, []))
    return np.sqrt(np.mean(np.diff(xs) ** 2)) if len(xs) >= 5 else np.nan


def win_typing(keys, a, b):
    return np.mean([keys.get(s, 0) for s in range(int(a), int(b))])


def main():
    dirs = sys.argv[1:]
    if not dirs:
        print(__doc__)
        sys.exit(1)

    rows = []
    print('Sessions:')
    for d in dirs:
        label = os.path.basename(d.rstrip('/')).split('_')[0]
        hr, rr, keys, t0, prompts = load_session(d)
        bl_hr = win_hr(hr, 0, BASELINE_SEC)
        bl_rm = win_rmssd(rr, 0, BASELINE_SEC)
        bl_ty = win_typing(keys, 0, BASELINE_SEC)
        print(f'  {label:6s} baseline HR={bl_hr:.1f} RMSSD={bl_rm:.1f} '
              f'typing/s={bl_ty:.2f}  prompts={len(prompts)}')
        for p in prompts:
            psec = ts(p['prompt_shown_utc']) - t0
            a = psec - LIKERT_WINDOW_SEC
            r = p['responses']
            a_hr = win_hr(hr, a, psec)
            a_rm = win_rmssd(rr, a, psec)
            rows.append({
                'a_hr': a_hr, 'a_rm': a_rm,
                'd_hr': a_hr - bl_hr, 'd_rm': a_rm - bl_rm,
                'focus': r['focus'], 'frust': r['frustration'], 'effort': r['effort'],
            })

    def corr(fx, fy):
        x = np.array([r[fx] for r in rows], float)
        y = np.array([r[fy] for r in rows], float)
        m = ~np.isnan(x) & ~np.isnan(y)
        if m.sum() < 4 or np.std(x[m]) == 0 or np.std(y[m]) == 0:
            return None
        r, p = stats.pearsonr(x[m], y[m])
        return r, p, m.sum()

    def fmt(c):
        return f'r={c[0]:+.2f} p={c[1]:.3f} N={c[2]}' if c else 'n/a'

    print(f'\nPooled observations: {len(rows)}')
    print('\nABSOLUTE pre-prompt feature vs affect   |   BASELINE-RELATIVE (minus first %ds)' % BASELINE_SEC)
    print('-' * 78)
    for aff in ['focus', 'frust', 'effort']:
        for raw, dev, name in [('a_hr', 'd_hr', 'HR'), ('a_rm', 'd_rm', 'RMSSD')]:
            print(f'  {name:5s} vs {aff:6s}: {fmt(corr(raw, aff)):26s} |  {fmt(corr(dev, aff))}')

    print('\nThe absolute column conflates between- and within-person variance.')
    print('The baseline-relative column centers each participant on their own')
    print('low-demand state and is the within-person estimate. Exploratory.')


if __name__ == '__main__':
    main()
