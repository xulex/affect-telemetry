# I'm Not a Robot

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20976219.svg)](https://doi.org/10.5281/zenodo.20976219)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**An open-source pipeline that repurposes cybersecurity UEBA telemetry to sense affect and protect Flow in knowledge work.**

reCAPTCHA already reads whether you are human from the way you move the mouse and
type. This project turns that idea around: it asks whether the same fine-motor
traces a security stack already collects can read *how you feel* while you work,
so an interface could one day sense and protect a worker's Flow instead of only
authenticating them.

This repository is the methodological contribution of a master's thesis (Steinbeis
University / Berlin School of Creative Leadership). It is the acquisition and
analysis code, the task materials, and the configuration. It contains **no
participant data** (see [ETHICS_AND_DATA.md](ETHICS_AND_DATA.md)).

## What it captures

Six time-aligned streams on one machine, on a shared UTC clock at a one-per-second grid:

| # | Stream | Script | Signal |
|---|--------|--------|--------|
| 1 | Heart rate / HRV | `polar_listener.py` | Polar H10 over BLE: heart rate + beat-to-beat RR intervals |
| 2 | Input dynamics | `input_dynamics.py` | keystroke and mouse **timing only** (never key content) |
| 3 | Application focus | `nsworkspace_monitor.py` | NSWorkspace focus changes |
| 4 | UEBA process/file events | `osquery_thesis.conf` (osquery daemon) | process and file events + snapshots |
| 5 | Screen + webcam | `obs_recorder.py` | OBS recording over WebSocket |
| 6 | Facial Action Units | `extract_facial_aus.py` | py-feat AUs extracted post-session on a GPU |

Streams 1 to 5 are live; stream 6 is derived afterward from the recording.

![Experimental setup and data acquisition pipeline: a participant at a desk wearing a Polar H10, with five live streams (heart rate, input dynamics, application focus, osquery process/file events, and screen+webcam recording) flowing into a per-session folder, all aligned to one shared UTC one-per-second grid, plus a sixth facial Action Unit stream derived afterward in the EU cloud.](docs/images/data-acquisition-pipeline.png)

### Facial Action Units (stream 6)

The webcam track is processed after the session on a GPU: frames are sampled,
faces detected and aligned, and per-frame Action Unit intensities and emotion
labels are written to `facial_au.csv`, re-anchored to the session's UTC clock.

![Facial Action Unit extraction pipeline: recording.mp4 is frame-sampled, then RetinaFace detects faces, MobileFaceNet finds landmarks, faces are aligned with HOG and geometry features, XGBoost estimates 20 Action Unit intensities, ResMaskingNet predicts 7 emotion labels, and the result is timestamp-reconstructed into facial_au.csv.](docs/images/facial-au-pipeline.png)

## Repository layout

| Path | Purpose |
|------|---------|
| `session_controller.py` | Operator GUI: consent, baseline, equipment check, prompts, debrief |
| `start_session.sh` | Orchestrator: spawns the five live streams + timer, slices osquery on exit |
| `polar_listener.py` `input_dynamics.py` `nsworkspace_monitor.py` `obs_recorder.py` `session_timer.py` | Acquisition streams |
| `preflight_check.py` | Pre-session health check (disk, osquery writing, OBS, focus/input, Polar) |
| `reslice_osquery.py` | Recovery tool: re-slice the global osquery log into a session |
| `osquery_thesis.conf` | osquery daemon config (copy to `/private/var/osquery/`) |
| `extract_facial_aus.py`, `colab/`, `azure/` | Facial AU extraction (GPU: local, Colab, or Azure) |
| `analysis/` | Post-session analysis (within-participant, baseline-relative) |
| `step8_materials/` | The "Meridian Consulting" task (instructions, reading docs, spreadsheet, debrief) |

Per-session output (`sessions/<id>/`) is **not** in git.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

The acquisition side targets **macOS** (Monterey 12.7, Python 3.11), because it
uses NSWorkspace and the macOS accessibility layer. It needs Accessibility,
Screen Recording, Camera, Microphone, and Full Disk Access (for osqueryd) granted
to the relevant apps. OBS (with WebSocket) and the osquery daemon are installed
separately. Facial AU extraction runs in a **separate GPU environment**
(`requirements-au.txt`); see `azure/` and `colab/`.

## Configuration

All scripts resolve their working directory from the repository location by
default, so a fresh clone runs without edits. To point them at a different data
root, set one environment variable:

```bash
export THESIS_DIR=/path/to/your/checkout
```

The osquery daemon config is the one exception: `osquery_thesis.conf` is read by
the daemon as strict JSON and cannot use environment variables, so it ships as a
**template**. Before deploying it, replace the placeholders:

- `__THESIS_DIR__` → the absolute path to your checkout
- `__USER1__`, `__USER2__` → the macOS account names whose own activity should be
  filtered out of the file-event stream (e.g., the researcher and operator accounts)

then copy it to `/private/var/osquery/osquery.conf` and restart the daemon.

## Running a session

1. Reboot the machine at the start of a session day (re-establishes the osquery
   Endpoint Security subscription and any drifted permissions).
2. Run `preflight_check.py` until all green.
3. Launch `session_controller.py`. It handles consent, baseline, the equipment
   check, and spawns `start_session.sh`, which runs the five live streams and the
   timer. Likert affect prompts fire mid-task; a debrief closes the session.
4. After the session, push `recording.mp4` to a GPU and run the AU extraction.

A complete session produces, per participant, the physiological, behavioral,
focus, process, recording, and (post-hoc) facial streams, plus consent, survey,
and metadata files.

## Analysis

`analysis/analyze_n1.py` builds the 1 Hz master grid and computes within-session
relationships. The headline method is **baseline-relative**: every feature is
expressed as a deviation from the participant's own low-demand baseline, because
between-person variance otherwise swamps the within-person signal. The pooled
scripts (`analyze_baseline_pooled.py`, `analyze_recovery.py`) aggregate across
sessions.

### Detecting AI-tool use

A separate three-layer detector (`analysis/detect_ai_usage.py` and friends)
reconstructs whether, and with which tool, a participant used an AI assistant
during the task. It merges three independent signals into one per-session
verdict: Layer 1 the focused-app and process logs, Layer 2 a time-windowed slice
of browser history matched against known assistant domains, and Layer 3
retrospective OCR of the screen recording.

![Three-layer AI-tool-use detector: Layer 1 reads focused-app, osquery, and clock logs; Layer 2 slices Safari history within the locked time window; Layer 3 runs screen-and-UI OCR on the recording; the three are merged into a per-session verdict (used AI, which tool, how long) written to ai_usage_summary.json.](docs/images/ai-use-detector.png)

## The study it was built for

A single-session calibration study: N = 13 analytical participants, 26 minutes of
realistic knowledge work (reading, spreadsheet analysis, synthesis, an email
deliverable), with three affect check-ins. The durable finding was that a mouse
slowing below a participant's own baseline tracks self-reported frustration; the
full results live in the thesis.

## Ethics and data

See [ETHICS_AND_DATA.md](ETHICS_AND_DATA.md). No participant data is included.
Input capture is timing-only. The task materials contain the answer key, so if
you replicate the task, recruit participants who have not seen this repository.

## Citation

See [CITATION.cff](CITATION.cff). Please cite the software and the thesis if you
build on this work. A Zenodo DOI will be added on archival.

## License

[MIT](LICENSE) © 2026 Norton Amato.
