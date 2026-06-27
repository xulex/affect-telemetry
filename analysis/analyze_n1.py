"""
analyze_n1.py
=============

N=1 cross-stream analysis on a captured session directory.

Aligns the five session streams at 1 Hz cadence over the common
observation window, then runs:
  1. Pairwise correlations between physiology, behavior, and facial AUs
  2. Comparison against the prior pilot reported in PROJECT_HANDOFF
  3. Unsupervised state discovery (k=2 clustering on standardized features)
  4. Likert alignment: per-prompt feature windows and pre/post comparisons

USAGE
-----
    python analyze_n1.py SESSION_DIR
    python analyze_n1.py SESSION_DIR --facial-au facial_au_5min.csv
    python analyze_n1.py SESSION_DIR --no-cluster   # skip clustering step

EXPECTED SESSION LAYOUT
-----------------------
    SESSION_DIR/
        polar.jsonl
        input.jsonl
        focused_app.jsonl
        osquery.jsonl
        facial_au.csv          (or facial_au_5min.csv etc, auto-detected)
        survey.jsonl           (optional; enables Likert alignment section)
"""

import os
import sys
import json
import glob
import email
import argparse
from email.utils import parsedate_to_datetime
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from scipy import stats


# =============================================================================
# File discovery
# =============================================================================

REQUIRED_JSONL = ['polar.jsonl', 'input.jsonl', 'focused_app.jsonl']
OPTIONAL_JSONL = ['osquery.jsonl']  # absent for sessions with a dead ES subscription
LIKERT_WINDOW_SEC = 60   # seconds before/after prompt to average features
BASELINE_SEC = 90        # first N seconds used as an in-session baseline proxy.
                         # NOTE: this is a proxy, not a true pre-task rest baseline.
                         # The consent/name-entry period was not recorded (streams
                         # start at task t=0). The first 90 s (early reading block)
                         # is the lowest-demand window available in the captured data.
POSTTASK_MIN_SEC = 60    # minimum length for a post-task wind-down window to count
FACIAL_AU_CANDIDATES = ['facial_au.csv', 'facial_au_5min.csv']


def find_facial_au_file(session_dir, override=None):
    """Locate the facial AU CSV. Tries override, then known names, then glob."""
    if override:
        path = os.path.join(session_dir, override)
        if os.path.exists(path):
            return path
        if os.path.exists(override):
            return override
        return None
    for name in FACIAL_AU_CANDIDATES:
        path = os.path.join(session_dir, name)
        if os.path.exists(path):
            return path
    matches = glob.glob(os.path.join(session_dir, 'facial_au*.csv'))
    return matches[0] if matches else None


def validate_session(session_dir, facial_au_override=None):
    """Confirm all required files exist. Returns dict of resolved paths."""
    paths = {}
    missing = []
    for fname in REQUIRED_JSONL:
        p = os.path.join(session_dir, fname)
        if os.path.exists(p):
            paths[fname.replace('.jsonl', '')] = p
        else:
            missing.append(fname)

    # osquery is OPTIONAL (a dead ES subscription can leave a session without it).
    for fname in OPTIONAL_JSONL:
        p = os.path.join(session_dir, fname)
        if os.path.exists(p):
            paths[fname.replace('.jsonl', '')] = p

    # Facial AU is OPTIONAL. When it has not been extracted yet (Azure still
    # processing) the rest of the analysis still runs on the live streams.
    au_path = find_facial_au_file(session_dir, facial_au_override)
    if au_path:
        paths['facial_au'] = au_path

    if missing:
        print('ERROR: missing required files in session directory:')
        for m in missing:
            print(f'  - {os.path.join(session_dir, m)}')
        print(f'\nSession dir contents:')
        if os.path.isdir(session_dir):
            for f in sorted(os.listdir(session_dir)):
                size = os.path.getsize(os.path.join(session_dir, f))
                print(f'  {f}  ({size:,} bytes)')
        else:
            print(f'  (not a directory)')
        sys.exit(1)

    print('Resolved session files:')
    for k, v in paths.items():
        size_mb = os.path.getsize(v) / 1e6
        print(f'  {k:15s} {v}  ({size_mb:.2f} MB)')
    print()
    return paths


# =============================================================================
# Stream loaders
# =============================================================================

def parse_ts(s):
    return datetime.fromisoformat(s).timestamp()


def load_polar(path):
    records = [json.loads(l) for l in open(path)]
    df = pd.DataFrame([{
        'ts': parse_ts(r['timestamp_utc']),
        'hr': r['heart_rate_bpm'],
        'rr_mean': np.mean(r['rr_intervals_ms']) if r['rr_intervals_ms'] else np.nan,
    } for r in records])
    rr_events = []
    for r in records:
        base = parse_ts(r['timestamp_utc'])
        for rr in r['rr_intervals_ms']:
            rr_events.append({'ts': base, 'rr': rr})
    rr_df = pd.DataFrame(rr_events) if rr_events else pd.DataFrame(columns=['ts', 'rr'])
    return df, rr_df


def load_input(path):
    records = [json.loads(l) for l in open(path)]
    return pd.DataFrame([{
        'ts': parse_ts(r['timestamp_utc']),
        'type': r['type'],
        'x': r.get('x'),
        'y': r.get('y'),
    } for r in records])


def load_focused_app(path):
    records = [json.loads(l) for l in open(path)]
    return pd.DataFrame([{
        'ts': parse_ts(r['timestamp_utc']),
        'app': r['bundle_name'],
    } for r in records])


def load_osquery(path):
    records = [json.loads(l) for l in open(path)]
    return pd.DataFrame([
        {'ts': r['unixTime'], 'name': r['name']}
        for r in records if r.get('name') == 'process_events_stream'
    ])


def load_facial_au(path, session_dir=None):
    # .copy() defragments the 175-column DataFrame that read_csv builds via
    # repeated internal inserts, preventing PerformanceWarning when we add 'ts'.
    df = pd.read_csv(path).copy()
    session_dir = session_dir or os.path.dirname(os.path.abspath(path))
    if df['timestamp_utc'].isna().all():
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from session_timing import repair_timestamps_from_chunks, resolve_recording_anchor

        anchor, src = resolve_recording_anchor(Path(session_dir))
        print(f'  facial_au timestamps missing — reconstructing (anchor: {src})')
        df = repair_timestamps_from_chunks(df, Path(session_dir))
    # tz-aware Series.astype('int64') returns microseconds, not nanoseconds.
    # Use .timestamp() for reliable unix-seconds conversion.
    df['ts'] = pd.to_datetime(df['timestamp_utc'], utc=True, format='ISO8601').map(
        lambda x: x.timestamp()
    )
    return df[df['FaceScore'] > 0.5].copy()


def load_survey(session_dir):
    """Load survey.jsonl and return submitted Likert prompts as a list of dicts.

    Each dict has:
        ts              unix seconds of prompt_shown_utc (when participant saw it)
        response_ts     unix seconds of response_utc
        nominal_min     scheduled minute (4, 12, or 19)
        focus           1-7
        frustration     1-7
        effort          1-7
        outcome         'submitted' | 'dismissed'
        response_latency_ms
    Only submitted prompts are returned (dismissed have no usable DV).
    Returns empty list if survey.jsonl is absent or contains no submitted Likerts.
    """
    path = os.path.join(session_dir, 'survey.jsonl')
    if not os.path.exists(path):
        return []
    prompts = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get('type') == 'prompt' and r.get('prompt_type') == 'likert':
                if r.get('outcome') == 'submitted' and r.get('responses'):
                    prompts.append({
                        'ts':                  parse_ts(r['prompt_shown_utc']),
                        'response_ts':         parse_ts(r['response_utc']),
                        'nominal_min':         r['nominal_min'],
                        'focus':               r['responses']['focus'],
                        'frustration':         r['responses']['frustration'],
                        'effort':              r['responses']['effort'],
                        'outcome':             r['outcome'],
                        'response_latency_ms': r.get('response_latency_ms'),
                    })
    return sorted(prompts, key=lambda x: x['ts'])


def load_email_send_ts(session_dir):
    """Return the email send time (unix seconds) from email-*.eml, or None.

    The participant's sent recommendation email carries a Date header. This is a
    true task-completion event, far more reliable than inferring completion from
    the last keystroke (a typing pause is not the same as 'done'). Used to define
    the post-task wind-down window precisely.
    """
    # Prefer the canonical email-<SID>.eml, but fall back to any *email*.eml.
    # Some sessions were saved with inconsistent names (email.eml, p14email.eml)
    # by the operator account; we still want their send timestamp.
    matches = (glob.glob(os.path.join(session_dir, 'email-*.eml'))
               or glob.glob(os.path.join(session_dir, '*email*.eml')))
    if not matches:
        return None
    try:
        with open(matches[0]) as f:
            msg = email.message_from_file(f)
        d = msg.get('Date')
        if not d:
            return None
        return parsedate_to_datetime(d).timestamp()
    except Exception:
        return None


# =============================================================================
# 1 Hz master grid
# =============================================================================

def build_master_grid(polar_df, rr_df, inp_df, app_df, oq_df, au_df):
    starts = [polar_df['ts'].min(), inp_df['ts'].min(), app_df['ts'].min()]
    ends = [polar_df['ts'].max(), inp_df['ts'].max(), app_df['ts'].max()]
    if au_df is not None:
        starts.append(au_df['ts'].min())
        ends.append(au_df['ts'].max())
    t_start = max(starts)
    t_end = min(ends)
    duration = t_end - t_start

    print(f'Common window: {duration:.1f} sec ({duration/60:.2f} min)')
    print(f'  Start UTC: {datetime.fromtimestamp(t_start, timezone.utc).isoformat()}')
    print(f'  End   UTC: {datetime.fromtimestamp(t_end, timezone.utc).isoformat()}')

    if duration < 30:
        print('\nWARNING: common window is under 30 seconds.')
        print('At least one stream may be misaligned or truncated.')

    bins = np.arange(0, int(duration) + 1)
    grid = pd.DataFrame({'sec': bins})

    # HR per second
    polar_df = polar_df.copy()
    polar_df['sec'] = (polar_df['ts'] - t_start).astype(int)
    hr_by_sec = polar_df.groupby('sec').agg(
        hr=('hr', 'mean'),
        rr_mean=('rr_mean', 'mean'),
    ).reset_index()
    grid = grid.merge(hr_by_sec, on='sec', how='left')

    # RMSSD over 30s rolling window
    rr_df = rr_df.copy()
    rr_df['sec'] = (rr_df['ts'] - t_start).astype(int)
    rmssd_per_sec = {}
    for s in bins:
        window = rr_df[(rr_df['sec'] >= s - 15) & (rr_df['sec'] <= s + 15)]
        if len(window) >= 5:
            diffs = np.diff(window['rr'].values)
            rmssd_per_sec[s] = np.sqrt(np.mean(diffs ** 2))
        else:
            rmssd_per_sec[s] = np.nan
    grid['rmssd'] = grid['sec'].map(rmssd_per_sec)

    # Input dynamics per second
    inp_df = inp_df.copy()
    inp_df['sec'] = (inp_df['ts'] - t_start).astype(int)
    inp_df = inp_df[(inp_df['sec'] >= 0) & (inp_df['sec'] <= int(duration))]

    key_press = inp_df[inp_df['type'] == 'key_press'].groupby('sec').size()
    mouse_move = inp_df[inp_df['type'] == 'mouse_move'].groupby('sec').size()
    mouse_click = inp_df[inp_df['type'] == 'mouse_click'].groupby('sec').size()

    mouse_only = inp_df[inp_df['type'] == 'mouse_move'].sort_values('ts').copy()
    mouse_only['dx'] = mouse_only['x'].diff()
    mouse_only['dy'] = mouse_only['y'].diff()
    mouse_only['dist'] = np.sqrt(mouse_only['dx']**2 + mouse_only['dy']**2)
    mouse_dist = mouse_only.groupby('sec')['dist'].sum()

    grid['typing'] = grid['sec'].map(key_press).fillna(0)
    grid['mouse_events'] = grid['sec'].map(mouse_move).fillna(0)
    grid['mouse_distance'] = grid['sec'].map(mouse_dist).fillna(0)
    grid['clicks'] = grid['sec'].map(mouse_click).fillna(0)

    # Focused app per second (forward-fill from activations)
    app_df = app_df.copy().sort_values('ts')
    app_df['sec'] = (app_df['ts'] - t_start).astype(int)
    app_by_sec = {}
    last = app_df.iloc[0]['app'] if len(app_df) else None
    for s in bins:
        matches = app_df[app_df['sec'] == s]
        if len(matches) > 0:
            last = matches.iloc[-1]['app']
        app_by_sec[s] = last
    grid['app'] = grid['sec'].map(app_by_sec)

    # osquery spawn rate per second
    if len(oq_df):
        oq_df = oq_df.copy()
        oq_df['sec'] = (oq_df['ts'] - t_start).astype(int)
        oq_per_sec = oq_df.groupby('sec').size()
        grid['process_spawns'] = grid['sec'].map(oq_per_sec).fillna(0)
    else:
        grid['process_spawns'] = 0

    # Facial AUs per second (only when the AU stream is present)
    if au_df is not None:
        au_df = au_df.copy()
        au_df['sec'] = (au_df['ts'] - t_start).astype(int)
        au_cols = ['AU01','AU02','AU04','AU05','AU06','AU07','AU09','AU10','AU11',
                   'AU12','AU14','AU15','AU17','AU20','AU23','AU24','AU25','AU26',
                   'AU28','AU43']
        emo_cols = ['anger','disgust','fear','happiness','sadness','surprise','neutral']
        available = [c for c in au_cols + emo_cols if c in au_df.columns]
        au_by_sec = au_df.groupby('sec')[available].mean().reset_index()
        grid = grid.merge(au_by_sec, on='sec', how='left')

    grid = grid[(grid['sec'] >= 0) & (grid['sec'] <= int(duration))].copy()
    return grid, duration


# =============================================================================
# Analysis steps
# =============================================================================

def print_correlations(grid):
    print('\n' + '=' * 70)
    print(f'CORRELATIONS (1-Hz grid, N={len(grid)} seconds)')
    print('=' * 70)

    pairs = [
        ('hr', 'typing',          'HR vs typing intensity'),
        ('hr', 'mouse_distance',  'HR vs mouse distance'),
        ('hr', 'mouse_events',    'HR vs mouse-event count'),
        ('hr', 'clicks',          'HR vs mouse clicks'),
        ('AU04', 'hr',            'AU04 (brow lowerer) vs HR'),
        ('AU17', 'hr',            'AU17 (chin raise) vs HR'),
        ('AU23', 'hr',            'AU23 (lip tightener) vs HR'),
        ('AU24', 'hr',            'AU24 (lip pressor) vs HR'),
        ('AU04', 'typing',        'AU04 vs typing intensity'),
        ('AU04', 'mouse_distance','AU04 vs mouse distance'),
        ('rmssd', 'typing',       'HRV (RMSSD) vs typing'),
        ('rmssd', 'mouse_distance','HRV (RMSSD) vs mouse distance'),
    ]

    print(f'\n  {"Pair":40s} {"r":>8s} {"p":>10s} {"N":>5s}')
    print('  ' + '-' * 68)
    for a, b, label in pairs:
        if a not in grid.columns or b not in grid.columns:
            print(f'  {label:40s} {"--":>8s} {"--":>10s} {"--":>5s}  (col missing)')
            continue
        sub = grid[[a, b]].dropna()
        if len(sub) < 5:
            print(f'  {label:40s} {"--":>8s} {"--":>10s} {len(sub):>5d}')
            continue
        r, p = stats.pearsonr(sub[a], sub[b])
        sig = ' *' if p < 0.05 else ('  .' if p < 0.10 else '   ')
        print(f'  {label:40s} {r:>+8.3f} {p:>10.4f} {len(sub):>5d}{sig}')


def print_pilot_comparison(grid):
    print('\n' + '=' * 70)
    print('PRIOR 30-SECOND PILOT (from PROJECT_HANDOFF) vs CURRENT')
    print('=' * 70)
    print('\n  Metric                              Prior (30s)    Current')
    print('  ' + '-' * 65)
    prior = {
        'HR vs typing intensity':  -0.54,
        'HR vs mouse movement':    +0.58,
        'AU04 vs HR':              -0.49,
        'AU17 vs HR':              +0.40,
    }
    pairs = [
        ('HR vs typing intensity', 'hr', 'typing'),
        ('HR vs mouse movement',   'hr', 'mouse_distance'),
        ('AU04 vs HR',             'AU04', 'hr'),
        ('AU17 vs HR',             'AU17', 'hr'),
    ]
    for label, a, b in pairs:
        if a not in grid.columns or b not in grid.columns:
            continue
        sub = grid[[a, b]].dropna()
        if len(sub) < 5:
            continue
        r, p = stats.pearsonr(sub[a], sub[b])
        print(f'  {label:38s} {prior[label]:>+7.2f}      {r:>+7.2f}  (p={p:.3f})')


def print_clustering(grid):
    try:
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print('\nNOTE: sklearn not installed. Skipping clustering step.')
        print('Install with: pip install scikit-learn')
        return

    print('\n' + '=' * 70)
    print('TWO-STATE CLUSTERING (the "two distinguishable states" hypothesis)')
    print('=' * 70)

    feat_cols = ['hr', 'rmssd', 'typing', 'mouse_distance',
                 'AU04', 'AU17', 'AU23', 'AU24']
    available = [c for c in feat_cols if c in grid.columns]
    feat = grid[available].dropna().copy()
    if len(feat) < 30:
        print(f'\nNot enough complete observations to cluster (N={len(feat)} < 30)')
        return

    X = StandardScaler().fit_transform(feat)
    km = KMeans(n_clusters=2, n_init=10, random_state=42).fit(X)
    feat['state'] = km.labels_

    print(f'\n  N seconds clustered: {len(feat)}')
    print(f'  State 0 size: {(feat["state"]==0).sum()}, '
          f'State 1 size: {(feat["state"]==1).sum()}')
    print('\n  Per-state means (raw units):')
    print(feat.groupby('state')[available].mean().round(2).to_string())

    print('\n  Feature separation (Welch t-test between states):')
    print(f'  {"Feature":20s} {"State 0":>10s} {"State 1":>10s} {"diff":>8s} {"p":>10s}')
    print('  ' + '-' * 62)
    for f in available:
        s0 = feat[feat['state'] == 0][f]
        s1 = feat[feat['state'] == 1][f]
        t, p = stats.ttest_ind(s0, s1, equal_var=False)
        sig = ' *' if p < 0.05 else ('  .' if p < 0.10 else '')
        print(f'  {f:20s} {s0.mean():>10.2f} {s1.mean():>10.2f} '
              f'{s1.mean()-s0.mean():>+8.2f} {p:>10.4f}{sig}')

    feat_with_app = feat.copy()
    feat_with_app['app'] = grid.loc[feat.index, 'app'].values
    print('\n  State distribution by focused app (% of seconds per app):')
    crosstab = pd.crosstab(feat_with_app['app'], feat_with_app['state'],
                            normalize='index') * 100
    print(crosstab.round(1).to_string())


def print_likert_analysis(grid, prompts, t_start):
    """Two sub-sections: window means aligned to each prompt, then pre/post contrast.

    grid['sec'] is seconds since t_start (the common-window start).
    prompts[i]['ts'] is unix seconds of prompt_shown_utc.
    We convert each prompt timestamp to grid-seconds and slice the window.
    """
    print('\n' + '=' * 70)
    print('LIKERT ALIGNMENT')
    print('=' * 70)

    if not prompts:
        print('\n  No submitted Likert prompts found in survey.jsonl.')
        print('  (All prompts dismissed, or survey.jsonl absent.)')
        return

    feat_cols = ['hr', 'rmssd', 'typing', 'mouse_distance',
                 'AU04', 'AU17', 'AU23', 'AU24']
    available = [c for c in feat_cols if c in grid.columns]

    print(f'\n  Window: ±{LIKERT_WINDOW_SEC}s around prompt_shown_utc')
    print(f'  N submitted prompts: {len(prompts)}\n')

    # ── Section A: window means per prompt ──────────────────────────────────
    print('  A. Feature means in ±{w}s window around each prompt'.format(
        w=LIKERT_WINDOW_SEC))
    print()
    header = f'  {"Prompt":>8s}  {"focus":>5s}  {"frust":>5s}  {"effort":>5s}  '
    header += '  '.join(f'{c:>7s}' for c in available)
    print(header)
    print('  ' + '-' * (len(header) - 2 + len(available) * 2))

    for p in prompts:
        p_sec = p['ts'] - t_start
        lo = p_sec - LIKERT_WINDOW_SEC
        hi = p_sec + LIKERT_WINDOW_SEC
        window = grid[(grid['sec'] >= lo) & (grid['sec'] <= hi)]
        if len(window) == 0:
            print(f'  min {p["nominal_min"]:>4.0f}    (prompt outside grid window)')
            continue
        means = window[available].mean()
        row = (f'  min {p["nominal_min"]:>4.0f}  '
               f'{p["focus"]:>5d}  {p["frustration"]:>5d}  {p["effort"]:>5d}  ')
        row += '  '.join(f'{means.get(c, float("nan")):>7.3f}' for c in available)
        n_obs = window[available].dropna(how='all').shape[0]
        row += f'  (N={n_obs}s)'
        print(row)

    # ── Section B: pre vs post contrast ─────────────────────────────────────
    print()
    print(f'  B. Pre/post contrast ({LIKERT_WINDOW_SEC}s before vs {LIKERT_WINDOW_SEC}s after prompt)')
    print(f'     Positive diff = higher AFTER prompt than before.\n')
    print(f'  {"Prompt":>8s}  {"feature":>16s}  {"pre mean":>9s}  {"post mean":>9s}  {"diff":>7s}  {"p":>8s}')
    print('  ' + '-' * 72)

    for p in prompts:
        p_sec = p['ts'] - t_start
        pre  = grid[(grid['sec'] >= p_sec - LIKERT_WINDOW_SEC) & (grid['sec'] < p_sec)]
        post = grid[(grid['sec'] >= p_sec) & (grid['sec'] < p_sec + LIKERT_WINDOW_SEC)]
        first = True
        for col in available:
            pre_vals  = pre[col].dropna()
            post_vals = post[col].dropna()
            if len(pre_vals) < 3 or len(post_vals) < 3:
                continue
            _, pval = stats.ttest_ind(pre_vals, post_vals, equal_var=False)
            diff = post_vals.mean() - pre_vals.mean()
            sig  = ' *' if pval < 0.05 else ('  .' if pval < 0.10 else '   ')
            label = f'min {p["nominal_min"]:>4.0f}' if first else ' ' * 8
            first = False
            print(f'  {label:>8s}  {col:>16s}  '
                  f'{pre_vals.mean():>9.3f}  {post_vals.mean():>9.3f}  '
                  f'{diff:>+7.3f}  {pval:>8.4f}{sig}')
        if not first:
            print()

    # ── Section C: composite affect score vs session-mean features ──────────
    if len(prompts) > 1:
        print(f'  C. Composite affect (focus - frustration) vs session-mean features')
        print(f'     (exploratory; N={len(prompts)} points only — treat as directional)\n')
        rows = []
        for p in prompts:
            p_sec = p['ts'] - t_start
            lo = p_sec - LIKERT_WINDOW_SEC
            hi = p_sec + LIKERT_WINDOW_SEC
            window = grid[(grid['sec'] >= lo) & (grid['sec'] <= hi)]
            if len(window) == 0:
                continue
            rec = {'affect': p['focus'] - p['frustration']}
            for col in available:
                vals = window[col].dropna()
                if len(vals):
                    rec[col] = vals.mean()
            rows.append(rec)
        if rows:
            adf = pd.DataFrame(rows)
            print(f'  {"Feature":>16s}  {"r":>8s}  note')
            print('  ' + '-' * 45)
            for col in available:
                if col not in adf.columns or adf[col].isna().all():
                    continue
                sub = adf[['affect', col]].dropna()
                if len(sub) < 2:
                    print(f'  {col:>16s}  {"--":>8s}  (N<2)')
                    continue
                if sub['affect'].std() == 0 or sub[col].std() == 0:
                    print(f'  {col:>16s}  {"--":>8s}  (zero variance)')
                    continue
                r, pval = stats.pearsonr(sub['affect'], sub[col])
                print(f'  {col:>16s}  {r:>+8.3f}  p={pval:.3f}, N={len(sub)}')
    elif len(prompts) == 1:
        print(f'  C. Composite affect: N=1 submitted prompt — correlation not computable.')


def print_baseline_analysis(grid, prompts, t_start, email_send_ts=None):
    """In-session baseline-proxy analysis.

    Uses the first BASELINE_SEC seconds (early reading block) as a per-participant
    baseline proxy, then expresses each prompt's pre-window features as deviations
    from it. Also detects a post-task wind-down window (after the last keystroke)
    and reports the same features there. Both are low-demand reference states the
    active-task windows can be compared against.

    This addresses the single-session baseline limitation: centering each
    participant on their own low-demand state removes between-person offsets that
    otherwise confound pooled correlations. The pre-task window is a proxy because
    the true consent-period telemetry was never recorded.
    """
    print('\n' + '=' * 70)
    print('BASELINE-PROXY ANALYSIS')
    print('=' * 70)

    feats = [c for c in ['hr', 'rmssd', 'typing', 'mouse_distance',
                         'AU04', 'AU17', 'AU23', 'AU24'] if c in grid.columns]

    base = grid[grid['sec'] < BASELINE_SEC]
    if len(base) == 0:
        print('\n  No data in the baseline window.')
        return
    base_mean = base[feats].mean()

    print(f'\n  Pre-task baseline = first {BASELINE_SEC}s (proxy; early reading block).')
    print('  Baseline means: ' +
          '  '.join(f'{c}={base_mean[c]:.2f}' for c in feats if pd.notna(base_mean[c])))

    # Post-task wind-down: reported by the actual email-send event when present.
    # NOTE the authoritative recovery arc (baseline -> task -> post-send) lives in
    # analyze_recovery.py, which uses raw stream extents. This grid is trimmed to
    # the common window (earliest-ending stream), so it can cut off the tail.
    dur = grid['sec'].max()
    if email_send_ts is not None:
        off = email_send_ts - t_start
        if off > dur:
            print(f'\n  Post-task window: email sent +{int(off)}s, after the '
                  f'{int(dur)}s common window ended (participant used all the time).')
        else:
            print(f'\n  Post-task window: email sent +{int(off)}s into the session. '
                  f'See analyze_recovery.py for the baseline/task/post-send arc.')
    else:
        print('\n  Post-task window: no email .eml found; see analyze_recovery.py.')

    # Per-prompt baseline-relative deviations.
    if prompts:
        print(f'\n  Prompt features as DEVIATION from the {BASELINE_SEC}s baseline:')
        print(f'  {"Prompt":>8s}  {"focus":>5s} {"frust":>5s} {"effort":>5s}  '
              + '  '.join(f'd_{c:<6s}'[:8] for c in feats))
        print('  ' + '-' * 96)
        for p in prompts:
            p_sec = p['ts'] - t_start
            win = grid[(grid['sec'] >= p_sec - LIKERT_WINDOW_SEC) & (grid['sec'] < p_sec)]
            if len(win) == 0:
                continue
            dev = win[feats].mean() - base_mean
            row = (f'  min {p["nominal_min"]:>4.0f}  '
                   f'{p["focus"]:>5d} {p["frustration"]:>5d} {p["effort"]:>5d}  ')
            row += '  '.join(f'{dev[c]:>+7.2f}' for c in feats)
            print(row)
        print('\n  (Pooled cross-participant baseline-relative correlations are in '
              'analyze_baseline_pooled.py.)')


def print_session_context(grid):
    print('\n' + '=' * 70)
    print('SESSION CONTEXT')
    print('=' * 70)
    print(f'\n  Time in each app (seconds in common window):')
    print(grid.groupby('app').size().sort_values(ascending=False).to_string())

    feat_cols = ['hr', 'typing', 'mouse_distance', 'AU04', 'AU17', 'AU23', 'AU24']
    available = [c for c in feat_cols if c in grid.columns]
    print('\n  Mean per app:')
    print(grid.groupby('app')[available].mean().round(2).to_string())


# =============================================================================
# Main
# =============================================================================

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('session_dir', type=str,
                    help='Path to session directory containing the JSONL streams + facial_au.csv')
    ap.add_argument('--facial-au', default=None,
                    help='Override facial AU CSV filename (auto-detected if omitted)')
    ap.add_argument('--no-cluster', action='store_true',
                    help='Skip the unsupervised clustering step')
    ap.add_argument('--force', action='store_true',
                    help='Run analysis even if session is marked EXCLUDED')
    args = ap.parse_args()

    session_dir = os.path.abspath(os.path.expanduser(args.session_dir))
    if not os.path.isdir(session_dir):
        print(f'ERROR: not a directory: {session_dir}')
        sys.exit(1)

    # Warn loudly if this session is marked excluded so it is never accidentally
    # included in analysis. The marker is written by the operator at QC time.
    excluded_marker = os.path.join(session_dir, 'EXCLUDED.txt')
    if os.path.exists(excluded_marker):
        print('=' * 70)
        print('WARNING: this session is marked EXCLUDED')
        with open(excluded_marker) as _f:
            print(_f.read().strip())
        print('=' * 70)
        print('\nPass --force to run analysis anyway.')
        if '--force' not in sys.argv:
            sys.exit(1)

    print(f'Session: {session_dir}\n')
    paths = validate_session(session_dir, args.facial_au)

    print('Loading streams...')
    polar_df, rr_df = load_polar(paths['polar'])
    inp_df = load_input(paths['input'])
    app_df = load_focused_app(paths['focused_app'])
    oq_df = load_osquery(paths['osquery']) if 'osquery' in paths else pd.DataFrame(columns=['ts', 'name'])
    au_df = (load_facial_au(paths['facial_au'], session_dir=session_dir)
             if 'facial_au' in paths else None)
    prompts = load_survey(session_dir)
    print(f'  polar:        {len(polar_df)} records, {len(rr_df)} RR intervals')
    print(f'  input:        {len(inp_df)} events')
    print(f'  focused_app:  {len(app_df)} activations')
    print(f'  osquery:      {len(oq_df)} process events'
          + ('' if 'osquery' in paths else ' (osquery.jsonl absent)'))
    if au_df is not None:
        print(f'  facial_au:    {len(au_df)} frames (FaceScore > 0.5)')
    else:
        print('  facial_au:    NOT PRESENT (AU not extracted yet) — '
              'AU-dependent sections skipped')
    print(f'  survey:       {len(prompts)} submitted Likert prompt(s)\n')

    grid, duration = build_master_grid(polar_df, rr_df, inp_df, app_df, oq_df, au_df)
    # t_start is needed by Likert alignment to convert UTC timestamps to grid-seconds.
    # It equals the maximum of all stream start times (the common-window start).
    starts = [polar_df['ts'].min(), inp_df['ts'].min(), app_df['ts'].min()]
    if au_df is not None:
        starts.append(au_df['ts'].min())
    t_start = max(starts)
    print(f'Master grid: {grid.shape}\n')

    print_correlations(grid)
    print_pilot_comparison(grid)
    if not args.no_cluster:
        print_clustering(grid)
    print_likert_analysis(grid, prompts, t_start)
    email_send_ts = load_email_send_ts(session_dir)
    print_baseline_analysis(grid, prompts, t_start, email_send_ts)
    print_session_context(grid)

    print('\n' + '=' * 70)
    print('DONE.')
    print('=' * 70)


if __name__ == '__main__':
    main()
