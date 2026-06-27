# Azure GPU scripts

| File | Purpose |
|------|---------|
| `install_feat_ubuntu.sh` | Pinned venv at `/opt/aubatch/venv` |
| `run_au_local.sh` | Chunked AU run (always uses venv) |
| `run_one_session.sh` | AzCopy blob download → process → upload |
| `SETUP_FIRST_TEST.md` | Short first-test checklist |

**SSH private key:** place `*.pem` here locally; files are gitignored.

Full guide: `!PROJDOCS/AZURE_AU_VM_GUIDE.md`

**Parallel batch (4 sessions / 4 VMs):** `azure/PARALLEL_BATCH.md` + `azure/parallel/*.sh`
