# Azure AU Pipeline: `timestamp_utc` Enrichment Missing on Output CSV

**Status:** Fixed in repo (2026-05-17) — re-run `colab/repair_au_timestamps.py` on existing CSVs without GPU
**Affected run:** `NA_self_20260516_20260516T103935Z` (20-min self-test, Azure NC4as T4 v3, Spain Central)
**Affected file:** `colab/au_benchmark_colab.py`
**Reported:** 2026-05-17

---

## What we observed

The output `facial_au_azure.csv` has the correct schema (175 columns including landmarks, AUs, emotions) and valid AU values, but **both `timestamp_utc` and `approx_time` columns are entirely null** across all 4,502 rows.

Other columns are populated correctly:

```python
df = pd.read_csv('facial_au_azure.csv')

df['frame'].notna().sum()          # 4502  ✓ populated
df['input'].notna().sum()          # 4502  ✓ populated (e.g. /tmp/colab_work/.../chunks/chunk_002.mp4)
df['FaceScore'].notna().sum()      # 4502  ✓ populated
df['AU04'].notna().sum()           # 4485  ✓ populated (minor face-not-detected drops)

df['timestamp_utc'].notna().sum()  # 0     ✗ FAILS
df['approx_time'].notna().sum()    # 0     ✗ FAILS
```

This matches the conditional language in §10 of `AZURE_AU_VM_GUIDE.md`:

> `timestamp_utc` | Present if `polar.jsonl` or session folder name has `YYYYMMDDTHHMMSSZ`

For the formal N=12 cohort, this needs to be reliably populated on every run, not conditional.

---

## Why it matters

The downstream cross-stream analyzer on the Mac (`analyze_n1.py`) aligns AU rows against the four behavioral streams (polar, input, focused_app, osquery) at 1-Hz cadence. Without `timestamp_utc`, alignment is impossible.

I patched `analyze_n1.py` with a reconstruction fallback that derives timestamps from `chunk_idx` (parsed from the `input` column) plus `frame / 30 fps`, anchored to either the session dir name Z-suffix or the earliest stream timestamp. It works, but introduces 1-5 seconds of slop because we don't know exactly when OBS started writing `recording.mp4` relative to the other streams. The Azure pipeline can do this more accurately because it has direct access to video metadata.

---

## Suggested fix

**Always populate `timestamp_utc` and `approx_time` for every row.**

In `colab/au_benchmark_colab.py`, during per-chunk CSV assembly or final concat, compute:

```python
approx_time_sec    = chunk_idx * CHUNK_SECONDS + (frame / source_fps)
timestamp_utc      = anchor_utc + timedelta(seconds=approx_time_sec)
```

Resolve `anchor_utc` in this priority order:

| Priority | Source | Rationale |
|----------|--------|-----------|
| 1 | `recording_start.txt` sidecar in session dir | Most accurate; written by Mac orchestrator when OBS WebSocket confirms recording started |
| 2 | `--recording-start <ISO8601>` CLI flag | For one-off processing where no sidecar exists |
| 3 | Session folder name `YYYYMMDDTHHMMSSZ` suffix | Session start (not recording start), usually within seconds |
| 4 | Earliest `polar.jsonl` timestamp | Current §10 fallback; least accurate |

Use `ffprobe recording.mp4` to determine actual `source_fps` rather than assuming 30. OBS settings can change between sessions.

`CHUNK_SECONDS` is already known from the chunking config (currently 300s).

---

## Acceptance criteria

After the fix, running on a fresh session should pass:

```python
df = pd.read_csv('facial_au_azure.csv')

assert df['timestamp_utc'].notna().all(), \
    "All rows must have timestamp_utc populated"

assert df['approx_time'].notna().all(), \
    "All rows must have approx_time populated"

# Sanity: first AU timestamp within 10 seconds of recording start
first_ts = pd.to_datetime(df['timestamp_utc'].iloc[0])
anchor   = pd.to_datetime(open(session_dir + '/recording_start.txt').read().strip())
assert abs((first_ts - anchor).total_seconds()) < 10

# Sanity: monotonic across the file
ts_series = pd.to_datetime(df['timestamp_utc'])
assert ts_series.is_monotonic_increasing
```

Add a single log line during processing that prints which anchor source was used:

```
[INFO] timestamp_utc anchor: recording_start.txt -> 2026-05-16T10:39:50.808Z
```

So the operator can verify at a glance which fallback (if any) was triggered.

---

## Related Mac-side change (recommended)

The cleanest long-term fix is to guarantee `recording_start.txt` exists for every session. This is a small change in `start_session.sh` / `obs_recorder.py` on the Mac side. When the OBS WebSocket confirms recording has started:

```bash
date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" > "$SESSION_DIR/recording_start.txt"
```

Then both the Azure pipeline and any future processing pipeline (e.g. PC fallback) have a single source of truth for the recording anchor. This file should be uploaded alongside `recording.mp4` when blob-staging a session for Azure processing.

---

## Workaround in place

`analyze_n1.py` (Mac-side) now detects all-null `timestamp_utc`, parses chunk index from the `input` column, and reconstructs timestamps using the priority order above. This is a fallback only — the proper fix belongs in the Azure pipeline so reconstruction logic doesn't have to live in three places (Azure, PC chunked wrapper, analyzer).

---

## Files / locations

| File | Role |
|------|------|
| `colab/au_benchmark_colab.py` | **Primary fix target** — add anchor resolution + timestamp population |
| `azure/run_one_session.sh` | May need updates to pass `recording_start.txt` from blob |
| `start_session.sh` (Mac) | Add `recording_start.txt` write on OBS recording start |
| `analyze_n1.py` (Mac) | Already patched with reconstruction fallback |
| `AZURE_AU_VM_GUIDE.md` §10 | Update once fix is verified: change "Present if..." to "Always present" |
