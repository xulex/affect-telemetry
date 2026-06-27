# Azure parallel GPU batch (4× T4, one session per VM)

Process **P06, P08, P09, P10** in parallel instead of ~40 h sequential on one VM.

| Session | ~26 min video | 1 VM wall time |
|---------|---------------|----------------|
| Each | ~6–13 h | one session |
| **4 VMs in parallel** | same | **~6–13 h total** |

---

## Architecture

```
Mac /Users/Shared/thesis-phase1
    │  mac_upload_all.sh  (code + 1 session per VM)
    ▼
VM 1 (P06)   VM 2 (P08)   VM 3 (P09)   VM 4 (P10)
    │            │            │            │
    └─ tmux au ──┴─ tmux au ──┴─ tmux au ──┴─ facial_au.csv each
    ▲
    │  mac_download_all.sh
Mac sessions/<ID>/facial_au.csv
```

---

## Step 0 — Stop the slow sequential job (if still running)

On **158.158.74.108**:

```bash
ssh -i ~/.ssh/xulex-keyAzure.pem xulex@158.158.74.108
tmux attach -t au_batch   # or au
# Ctrl+C to stop current session
tmux kill-session -t au_batch 2>/dev/null || tmux kill-session -t au 2>/dev/null || true
```

Resume later on a dedicated VM if needed; checkpoints live in `/tmp/colab_work/<SESSION_ID>/chunks/`.

---

## Step 1 — Create 3 more GPU VMs (Portal)

You have **vm-au-1** at `158.158.74.108`. Create **3 more** identical VMs:

| Setting | Value |
|---------|--------|
| Region | **Spain Central** (same as storage) |
| Size | **NC4as T4 v3** |
| Image | Ubuntu 22.04 LTS x64 Gen 2 |
| Security | **Standard** (not Trusted Launch) |
| Auth | Same SSH public key as vm-au-1 |
| Disk | 128 GB Premium |

Names (suggestion): `vm-thesis-au-2`, `vm-thesis-au-3`, `vm-thesis-au-4`

Note each **Public IP**. Request GPU quota if size is greyed out (4 vCPUs × 4 VMs = 16 vCPUs).

**First boot on each new VM** — install GPU driver once:

```bash
ssh -i ~/.ssh/xulex-keyAzure.pem xulex@<NEW_IP>
sudo apt-get update && sudo apt-get install -y linux-headers-$(uname -r) build-essential dkms
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update && sudo apt-get -y install cuda-drivers
sudo reboot
# after reboot:
nvidia-smi
```

vm-au-1 already has drivers if `nvidia-smi` works.

---

## Step 2 — Assign sessions (Mac)

```bash
cp /Users/Shared/thesis-phase1/azure/parallel/vm_assignments.env.example \
   /Users/Shared/thesis-phase1/azure/parallel/vm_assignments.env
```

Edit `vm_assignments.env` — **one line per VM** (IP + session):

```
158.158.74.108  P06_20260602T214915Z  vm-au-1
<IP-2>          P08_20260605T091258Z  vm-au-2
<IP-3>          P09_20260605T095250Z  vm-au-3
<IP-4>          P10_20260605T120652Z  vm-au-4
```

---

## Step 3 — Upload code + one session per VM (Mac)

```bash
chmod +x /Users/Shared/thesis-phase1/azure/parallel/*.sh

export THESIS=/Users/Shared/thesis-phase1
export KEY=~/.ssh/xulex-keyAzure.pem

bash /Users/Shared/thesis-phase1/azure/parallel/mac_upload_all.sh
```

---

## Step 4 — Start all workers (Mac)

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_start_all.sh
```

Each VM runs in **tmux session `au`**.

Monitor:

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_status_all.sh
```

Or attach to one VM:

```bash
ssh -i ~/.ssh/xulex-keyAzure.pem xulex@158.158.74.108
tmux attach -t au
```

Look for:

```
[INFO] timestamp_utc anchor: recording_start.txt -> ...
Wrote .../facial_au.csv (.... rows, 175 cols)
```

---

## Step 5 — Download results (Mac, when all done)

```bash
bash /Users/Shared/thesis-phase1/azure/parallel/mac_download_all.sh
```

Outputs land in:

```
/Users/Shared/thesis-phase1/sessions/<SESSION_ID>/facial_au.csv
```

---

## Step 6 — Stop all VMs (Portal)

**Stop (deallocate)** all 4 VMs when idle. ~$2–4/h while all 4 run; batch of 4 sessions ≈ **$15–50** total.

---

## Scripts reference

| Script | Where | Purpose |
|--------|-------|---------|
| `parallel/vm_assignments.env` | Mac | IP ↔ session map (gitignored) |
| `parallel/mac_upload_all.sh` | Mac | rsync code + 1 session per VM |
| `parallel/mac_start_all.sh` | Mac | tmux start on all VMs |
| `parallel/mac_status_all.sh` | Mac | tail logs / check tmux |
| `parallel/mac_download_all.sh` | Mac | scp CSVs back |
| `parallel/vm_run.sh` | VM | one session (venv-safe) |

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| SSH key permissions | `chmod 400 ~/.ssh/xulex-keyAzure.pem` |
| `kornia_rs` / no venv | `bash ~/thesis-phase1/azure/install_feat_ubuntu.sh` on that VM |
| One VM failed | Re-run upload + `mac_start_all.sh` for that line only |
| Resume chunk | Re-run `vm_run.sh`; skips existing `chunk_*.csv` |
| Timestamps null | Update code via `mac_upload_all.sh`; or `colab/repair_au_timestamps.py` on Mac |

---

## Optional: blob workflow (many VMs / no rsync)

Use `azure/run_one_session.sh` with AzCopy + `STORAGE_ACCOUNT` / `SAS` / `SESSION_ID` per VM instead of rsync. Same one-session-per-VM assignment.
