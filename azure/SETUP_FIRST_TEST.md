# Azure — first test (one 20-min session)

Process **one** full session folder (`recording.mp4` ~20 min) on a **single GPU VM**, then download `facial_au.csv`.

**Session used in examples:** `NA_self_20260516_20260516T103935Z`

---

## Overview

| Step | What |
|------|------|
| A | Azure resource group + storage + upload session folder |
| B | Request GPU quota (if needed) |
| C | Create one T4 GPU VM |
| D | Install py-feat stack on the VM |
| E | Copy repo scripts to VM (or git clone) |
| F | Download session from Blob → run chunked extraction → upload CSV |
| G | Stop VM (stop billing) |

**Time:** ~1–2 h setup + **~3–6 h** processing for 20 min video on T4 (similar to your PC).

**Cost:** roughly **$2–8** for one test VM day (T4 + disk); comes from your $1000 credit.

---

## A. Storage — upload session folder

### A1. Create resources (Azure Portal)

1. Go to [https://portal.azure.com](https://portal.azure.com)
2. **Create a resource** → **Storage account**
3. Settings:
   - **Resource group:** `rg-thesis-phase1` (create new)
   - **Storage account name:** globally unique, e.g. `thesisphase1data` (lowercase, no spaces)
   - **Region:** `West Europe` or `Germany West Central` (EU)
   - **Performance:** Standard
   - **Redundancy:** LRS (cheapest for temp data)
4. **Review + create**

### A2. Create blob container

1. Open the storage account → **Containers** → **+ Container**
2. Name: `sessions`
3. **Create**

### A3. Upload your session folder

1. Open container `sessions`
2. **Upload** → **Upload folder** (or use AzCopy below)
3. Upload the **entire** folder so the path looks like:

   ```
   sessions/NA_self_20260516_20260516T103935Z/recording.mp4
   sessions/NA_self_20260516_20260516T103935Z/polar.jsonl
   ... (other session files OK)
   ```

**Mac — AzCopy (optional, faster for large video):**

```bash
# Install: brew install azcopy  (or download from Microsoft)
azcopy login

STORAGE_ACCOUNT="thesisphase1data"   # yours
SESSION="NA_self_20260516_20260516T103935Z"
LOCAL=~/thesis-phase1/sessions/$SESSION

azcopy copy "$LOCAL" \
  "https://${STORAGE_ACCOUNT}.blob.core.windows.net/sessions/${SESSION}" \
  --recursive
```

### A4. SAS token for the VM (read/write one container)

1. Storage account → **Shared access signature**
2. Allowed services: **Blob**
3. Allowed resource types: **Container, Object**
4. Permissions: **Read, List, Write, Create**
5. Expiry: **7 days** (for testing)
6. **Generate SAS and connection string**
7. Copy the **SAS token** (starts with `?sv=...`) — you need it on the VM.

Save also:

- Storage account name: `thesisphase1data`
- Container: `sessions`

---

## B. GPU quota

1. **Subscriptions** → your subscription → **Usage + quotas**
2. Search: **NCASv3_T4** or **T4**
3. If **limit is 0**, click **Request increase** → ask for **4–8 vCPUs** in your region
4. Often approved in **24–48 h**; some subscriptions already have quota.

---

## C. Create GPU VM

### C1. Create VM (Portal)

1. **Create a resource** → **Virtual machine**
2. Basics:
   - **RG:** `rg-thesis-phase1`
   - **Name:** `vm-thesis-au-test`
   - **Region:** same as storage (EU)
   - **Image:** **Ubuntu Server 22.04 LTS Gen2**
   - **Size:** **NC4as T4 v3** (4 vCPU, 1× T4 16 GB) — pick any **T4** NC-series if that SKU is unavailable
   - **Authentication:** SSH public key (recommended) or password
3. Disks: default **Premium SSD** 128 GB is fine
4. Networking: default (SSH port 22)
5. **Create**

### C2. Note connection info

- **Public IP** of the VM
- SSH: `ssh azureuser@<PUBLIC_IP>` (username may be `azureuser` or what you chose)

### C3. Install NVIDIA driver (Portal extension)

1. VM → **Extensions** → **+ Add**
2. Search **NVIDIA GPU Driver Extension** (Linux) → Install → **Create**
3. Wait until **Provisioning succeeded** (reboot may happen)

---

## D. Install software on the VM

SSH into the VM, then:

```bash
# System packages
sudo apt-get update
sudo apt-get install -y git ffmpeg python3.11 python3.11-venv

# Clone your repo (if private, use upload via scp instead — see F2)
cd ~
git clone <YOUR_REPO_URL> thesis-phase1
# OR: scp -r ~/thesis-phase1 azureuser@<IP>:~/thesis-phase1

# Run pinned install (matches Colab/Windows stack)
bash ~/thesis-phase1/azure/install_feat_ubuntu.sh
```

Verify GPU:

```bash
nvidia-smi
source /opt/aubatch/venv/bin/activate
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

---

## E. Configure storage on VM

On the VM, set variables (replace with your values):

```bash
export STORAGE_ACCOUNT="thesisphase1data"
export CONTAINER="sessions"
export SAS="?sv=2022-11-02&ss=..."   # full SAS query string from portal
export SESSION_ID="NA_self_20260516_20260516T103935Z"
```

Optional: add to `~/.bashrc` for the test session.

Install AzCopy on VM:

```bash
curl -sL https://aka.ms/downloadazcopy-v10-linux | tar xz --strip-components=1 -C ~/bin azcopy_linux_amd64_*/azcopy 2>/dev/null || \
  sudo mkdir -p /usr/local/bin && \
  curl -sL https://aka.ms/downloadazcopy-v10-linux | sudo tar xz --strip-components=1 -C /usr/local/bin azcopy_linux_amd64_*/azcopy
azcopy --version
```

---

## F. Run the 20-min test

### F1. Download session from Blob

```bash
source /opt/aubatch/venv/bin/activate
export WORK=/data/sessions
mkdir -p "$WORK"

azcopy copy \
  "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${SESSION_ID}${SAS}" \
  "${WORK}/${SESSION_ID}" \
  --recursive

ls -lh "${WORK}/${SESSION_ID}/recording.mp4"
```

### F2. Run chunked AU extraction (progress bars)

```bash
cd ~/thesis-phase1
source /opt/aubatch/venv/bin/activate

python colab/au_benchmark_colab.py \
  --session-dir "${WORK}/${SESSION_ID}" \
  --input recording.mp4 \
  --fps 4 \
  --batch-size 4 \
  --progress \
  --output facial_au_azure.csv \
  --work-dir "/tmp/colab_work/${SESSION_ID}"
```

You should see:

- `Session chunks` tqdm (4 chunks for ~20 min)
- Frame progress per chunk
- `Wrote .../facial_au_azure.csv`

**If CUDA OOM:** use `--batch-size 1`.

### F3. Upload result back to Blob

```bash
azcopy copy \
  "${WORK}/${SESSION_ID}/facial_au_azure.csv" \
  "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${SESSION_ID}/facial_au_azure.csv${SAS}"
```

### F4. Download to Mac

```bash
azcopy copy \
  "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${SESSION_ID}/facial_au_azure.csv${SAS}" \
  ~/thesis-phase1/sessions/${SESSION_ID}/facial_au_azure.csv
```

---

## G. Stop billing

1. Portal → VM → **Stop** (deallocate) when finished
2. Optional: delete VM if test succeeded; keep storage for next runs
3. Revoke or let SAS expire

---

## QA checklist

| Check | Expected |
|-------|----------|
| `nvidia-smi` | T4 visible |
| `py-feat` | 0.6.1 |
| `numpy` | 1.26.x |
| Output columns | **688** |
| Rows (~20 min @ 4 fps) | ~**4800** (±10%) |
| `timestamp_utc` column | present |

```bash
python -c "
import pandas as pd
df = pd.read_csv('${WORK}/${SESSION_ID}/facial_au_azure.csv', nrows=5)
print('cols', len(df.columns), 'rows', len(pd.read_csv('${WORK}/${SESSION_ID}/facial_au_azure.csv')))
"
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No T4 size in portal | Request quota; try region **West Europe** |
| `nvidia-smi` not found | Install **NVIDIA GPU Driver Extension**, reboot VM |
| `np.mat` error | numpy 2.x — re-run `install_feat_ubuntu.sh` |
| Slow download | VM and storage in **same region** |
| SSH | Check NSG allows port 22 from your IP |

---

## Next: scale to 20 sessions

See `azure/SCALE_BATCH.md` (to be added) — multiple VMs, queue of session folders, same pattern.
