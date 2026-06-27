# Facial Action Unit extraction on Azure GPU VMs

Stream 6 of the pipeline (facial Action Units) is **not** captured live. It is
extracted after the session from `recording.mp4` on a CUDA GPU using py-feat.
This folder runs that step on **Azure NC-series T4 VMs**. The same scripts work on
any Ubuntu CUDA box; only the provisioning steps are Azure-specific.

A 26-minute recording at 4 fps takes roughly **30 to 65 minutes** on a single T4.
Many sessions run in parallel, one per VM (see [Scaling](#scaling-many-sessions-in-parallel)).

> **Credentials never live in this repo.** Your SSH private key (`*.pem`), the
> `.obs_credentials` file, and Azure SAS tokens are all gitignored. Keep the key
> in `~/.ssh/`, pass it with `-i`, and never commit it. Throughout this guide,
> `<VM_PUBLIC_IP>` is your VM's IP, `~/.ssh/azure-au.pem` is your key, and
> `azureuser` is the VM login you chose.

## Files in this folder

| File | Purpose |
|------|---------|
| `install_feat_ubuntu.sh` | One-shot installer: pinned py-feat venv at `/opt/aubatch/venv` |
| `run_au_local.sh` | Chunked AU run on a VM (always uses the venv) |
| `run_one_session.sh` | AzCopy blob download → extract → upload, end to end |
| `install_video_ubuntu.sh`, `VIDEO_BATCH.md` | AI-use video-review pipeline (optional) |
| `SETUP_FIRST_TEST.md` | Condensed one-session checklist |
| `PARALLEL_BATCH.md`, `parallel/` | Scale to N sessions across N VMs |

---

## Prerequisites

- An **Azure subscription** with **GPU quota** for an NC T4 SKU (see Step 2).
- One or more **session folders**, each containing at least `recording.mp4` and
  `recording_start.txt` (the latter anchors AU timestamps to the session clock).
- An **SSH key pair**. Create one if you do not have it:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/azure-au -C "azure-au"
  chmod 400 ~/.ssh/azure-au          # private key
  # ~/.ssh/azure-au.pub is the PUBLIC key you paste into the VM at creation
  ```
- `azcopy` on your Mac if you move data through Blob storage (`brew install azcopy`).

---

## Step-by-step: one session on one VM

### 1. Create storage and upload the recording

1. Azure Portal → **Create a resource → Storage account**.
   - Resource group: `rg-affect-telemetry` (new)
   - Name: globally unique, lowercase (e.g. `affecttelemetrydata`)
   - Region: an **EU** region for data residency (e.g. `West Europe`, `Germany West Central`)
   - Redundancy: **LRS** (cheapest for transient data)
2. In the account → **Containers → + Container** → name it `sessions`.
3. Upload the session folder (Portal upload, or AzCopy):
   ```bash
   az login            # or: azcopy login
   STORAGE_ACCOUNT="affecttelemetrydata"
   SESSION_ID="<SESSION_ID>"
   azcopy copy "$THESIS_DIR/sessions/$SESSION_ID" \
     "https://${STORAGE_ACCOUNT}.blob.core.windows.net/sessions/${SESSION_ID}" --recursive
   ```
4. Generate a short-lived **SAS token** (account → **Shared access signature**):
   services **Blob**, resource types **Container + Object**, permissions
   **Read/List/Write/Create**, expiry a few days. Copy the token (starts with `?sv=`).
   Treat it as a secret; do not commit it.

> Small studies can skip Blob entirely and `scp` the recording straight to the VM
> (Step 6). Blob is worth it when you process many sessions or large videos.

### 2. Confirm GPU quota

Portal → **Subscriptions → Usage + quotas** → search **NCASv3_T4** (or **T4**).
If the limit is 0, **Request increase** for 4 to 8 vCPUs in your region (one T4 VM
is 4 vCPUs). Approval is usually 24 to 48 hours.

### 3. Create the GPU VM

Portal → **Create a resource → Virtual machine**:

- Resource group: `rg-affect-telemetry`
- Name: e.g. `vm-au-1`
- Region: **same as the storage account**
- Image: **Ubuntu Server 22.04 LTS (Gen2)**
- Size: **NC4as T4 v3** (4 vCPU, 1× T4 16 GB). Any T4 NC-series works.
- Authentication: **SSH public key** → paste the contents of `~/.ssh/azure-au.pub`
- Disk: **Premium SSD 128 GB**
- Security type: **Standard** (not Trusted Launch, which complicates the driver)

After it boots, note the **public IP** and confirm SSH:
```bash
ssh -i ~/.ssh/azure-au.pem azureuser@<VM_PUBLIC_IP>
```

### 4. Install the NVIDIA driver

Easiest path: VM → **Extensions + applications → + Add → NVIDIA GPU Driver
Extension (Linux)** → install and wait for **Provisioning succeeded** (it reboots).

Then verify on the VM:
```bash
nvidia-smi          # the T4 should be listed
```
If `nvidia-smi` is missing, install the driver manually:
```bash
sudo apt-get update && sudo apt-get install -y linux-headers-$(uname -r) build-essential dkms
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update && sudo apt-get -y install cuda-drivers
sudo reboot
```

### 5. Install the py-feat environment

On the VM, get the code and run the pinned installer:
```bash
sudo apt-get install -y git ffmpeg python3.11 python3.11-venv
git clone https://github.com/xulex/affect-telemetry.git ~/affect-telemetry
bash ~/affect-telemetry/azure/install_feat_ubuntu.sh     # builds /opt/aubatch/venv
```
Verify the GPU is visible to PyTorch:
```bash
source /opt/aubatch/venv/bin/activate
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

### 6. Get the session onto the VM

From Blob (using the SAS token from Step 1):
```bash
export STORAGE_ACCOUNT="affecttelemetrydata" CONTAINER="sessions"
export SAS='?sv=...'                  # paste your token; keep it out of git
export SESSION_ID="<SESSION_ID>"
mkdir -p /data/sessions
azcopy copy \
  "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${SESSION_ID}${SAS}" \
  "/data/sessions/${SESSION_ID}" --recursive
```
Or copy straight from your Mac (no Blob):
```bash
scp -i ~/.ssh/azure-au.pem -r "$THESIS_DIR/sessions/<SESSION_ID>" \
    azureuser@<VM_PUBLIC_IP>:/data/sessions/
```

### 7. Run the extraction

```bash
cd ~/affect-telemetry
source /opt/aubatch/venv/bin/activate
python colab/au_benchmark_colab.py \
  --session-dir "/data/sessions/<SESSION_ID>" \
  --input recording.mp4 --fps 4 --batch-size 4 --progress \
  --output facial_au.csv --work-dir "/tmp/aubatch/<SESSION_ID>"
```
You should see per-chunk progress and finally `Wrote .../facial_au.csv`
(175 columns, about 5,800 rows for a 26-minute video). On a CUDA out-of-memory
error, use `--batch-size 1`. `run_au_local.sh` wraps these defaults; `run_one_session.sh`
adds the Blob download and upload around it.

### 8. Retrieve the result and stop billing

```bash
# back on your Mac
scp -i ~/.ssh/azure-au.pem \
  azureuser@<VM_PUBLIC_IP>:/data/sessions/<SESSION_ID>/facial_au.csv \
  "$THESIS_DIR/sessions/<SESSION_ID>/facial_au.csv"
```
Then **Stop (deallocate)** the VM in the Portal so it stops charging, and let the
SAS token expire.

---

## Scaling: many sessions in parallel

One session per VM is the unit of work. To process a batch, create N identical
VMs and assign one session to each:

1. Copy `parallel/vm_assignments.env.example` to `parallel/vm_assignments.env`
   (gitignored) and fill in one line per VM: `<VM_PUBLIC_IP>  <SESSION_ID>  <label>`.
2. Drive all VMs with the helper scripts:
   ```bash
   export KEY=~/.ssh/azure-au.pem
   bash parallel/mac_upload_all.sh     # rsync code + one session to each VM
   bash parallel/mac_start_all.sh      # start extraction in a tmux on each VM
   bash parallel/mac_status_all.sh     # monitor
   bash parallel/mac_download_all.sh   # pull every facial_au.csv back
   ```

Full walkthrough: [`PARALLEL_BATCH.md`](PARALLEL_BATCH.md). The optional
screen-recording AI-use review uses the same pattern: [`VIDEO_BATCH.md`](VIDEO_BATCH.md).

---

## Cost and time

| Item | Rough figure |
|------|--------------|
| NC4as T4 v3 | about US$0.50 to 1 per hour (region dependent) |
| One 26-min session | 30 to 65 minutes of GPU time |
| A batch of N sessions on N VMs | wall time of one session, cost ≈ N × hourly rate |

Always **deallocate** VMs when idle; a forgotten running VM is the main cost risk.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| T4 size greyed out | Request quota (Step 2); try region `West Europe` |
| `nvidia-smi` not found | Install the NVIDIA driver extension and reboot (Step 4) |
| `np.mat` / numpy errors | numpy 2.x leaked in; re-run `install_feat_ubuntu.sh` (pins 1.26.4) |
| Timestamps null in CSV | session is missing `recording_start.txt`; re-upload the full folder |
| Slow Blob transfer | keep the VM and storage account in the **same region** |
| SSH refused | the network security group must allow port 22 from your IP |
