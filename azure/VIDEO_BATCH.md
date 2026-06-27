# Azure video OCR batch (AI assistant detection from screen recordings)

Process `recording.mp4` on CPU VMs: sample frames during Safari/Chrome focus
windows and OCR the URL bar for AI domains (ChatGPT, Claude, Gemini, …).

**GPU not required.** Use cheap **Standard_D4s_v3** VMs, or reuse an idle GPU VM.

---

## What gets processed

Layer 1 (`detect_ai_usage.py`) flags **6 sessions** with `needs_video_review=True`:

| Session | Browser time |
|---------|----------------|
| <SESSION_ID> | ~9 min Safari |
| <SESSION_ID> | ~9 min |
| <SESSION_ID> | ~10 min |
| <SESSION_ID> | ~7 min |
| <SESSION_ID> | ~7 min |
| <SESSION_ID> | ~5 min |

Optional: P12, P15 (heavy Safari + osquery, no native focus time).

Wall time per session on CPU: **~5–15 min** (depends on browser-window count).

---

## Step 1 — Create CPU VMs (Portal) or reuse vm-au-1

**Recommended new VM** (cheaper than T4):

| Setting | Value |
|---------|--------|
| Region | **Spain Central** |
| Size | **Standard_D4s_v3** (4 vCPU, 16 GB) |
| Image | Ubuntu 22.04 LTS x64 Gen 2 |
| Security | Standard |
| Auth | Same SSH key as AU VMs |
| Disk | 64–128 GB |

Create 1–6 VMs, or reuse **vm-au-1** (`<VM_PUBLIC_IP>`) when GPU is idle.

No NVIDIA driver needed for video OCR.

---

## Step 2 — Assign sessions (Mac)

```bash
cp /Users/Shared/thesis-phase1/azure/parallel/vm_video_assignments.env.example \
   /Users/Shared/thesis-phase1/azure/parallel/vm_video_assignments.env
```

Edit — one session per VM line:

```
<VM_PUBLIC_IP>  <SESSION_ID>  vm-vid-1
<IP-2>          <SESSION_ID>  vm-vid-2
...
```

---

## Step 3 — Layer-1 reports on Mac (optional but fast)

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_prepare_video_reports.sh
```

---

## Step 4 — Upload code + recordings (Mac)

```bash
chmod +x /Users/Shared/thesis-phase1/azure/parallel/*video*.sh
chmod +x /Users/Shared/thesis-phase1/azure/install_video_ubuntu.sh

export KEY=~/.ssh/azure-au.pem
bash /Users/Shared/thesis-phase1/azure/parallel/mac_upload_video_all.sh
```

Uploads ~250 MB `recording.mp4` + `ai_usage_report.json` per session.

---

## Step 5 — Install stack on each VM (first time)

Runs automatically on first `vm_video_run.sh`, or manually:

```bash
ssh -i ~/.ssh/azure-au.pem azureuser@<IP>
bash ~/thesis-phase1/azure/install_video_ubuntu.sh
```

Installs: `ffmpeg`, `tesseract`, Python venv at `/opt/videobatch/venv`.

---

## Step 6 — Start workers (Mac)

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_start_video_all.sh
```

Each VM runs in **tmux session `video`**.

Monitor:

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_status_video_all.sh
```

Attach on one VM:

```bash
ssh -i $KEY azureuser@<IP>
tmux attach -t video
```

---

## Step 7 — Download results (Mac)

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_download_video_all.sh
```

Outputs:

```
sessions/<SESSION_ID>/ai_video_report.json
sessions/<SESSION_ID>/video_run.log
```

Key fields: `used_ai_web`, `ai_domains_seen`, `merged_used_ai`, `merged_confidence`.

---

## Step 8 — Deallocate VMs when idle

Stop/deallocate CPU VMs in Azure Portal to save credits.

---

## Scripts

| Script | Where | Purpose |
|--------|-------|---------|
| `install_video_ubuntu.sh` | VM | ffmpeg + tesseract + venv |
| `analysis/process_ai_video.py` | VM/Mac | frame OCR pipeline |
| `parallel/vm_video_assignments.env` | Mac | IP ↔ session (gitignored) |
| `parallel/mac_upload_video_all.sh` | Mac | rsync code + recording |
| `parallel/mac_start_video_all.sh` | Mac | tmux start |
| `parallel/mac_status_video_all.sh` | Mac | progress |
| `parallel/mac_download_video_all.sh` | Mac | pull reports |

---

## Local test (Mac, before Azure)

```bash
brew install ffmpeg tesseract
pip install pillow pytesseract

python3 /Users/Shared/thesis-phase1/analysis/detect_ai_usage.py \
  /Users/Shared/thesis-phase1/sessions/<SESSION_ID> --write-json

python3 /Users/Shared/thesis-phase1/analysis/process_ai_video.py \
  /Users/Shared/thesis-phase1/sessions/<SESSION_ID> --write-json
```

---

## Sequential batch on one VM

If only one VM is up, process sessions one at a time:

1. Put one line in `vm_video_assignments.env`
2. `mac_upload_video_all.sh` → `mac_start_video_all.sh`
3. Wait for DONE in `mac_status_video_all.sh`
4. `mac_download_video_all.sh`
5. Update assignments for next session; repeat
