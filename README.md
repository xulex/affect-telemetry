# I'm Not a Robot

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
