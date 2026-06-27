#!/usr/bin/env bash
# Generate ai_usage_report.json locally for all sessions (layer 1).
set -euo pipefail

THESIS="${THESIS:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)}"
python3 "$THESIS/analysis/detect_ai_usage.py" --all --write-json
echo "Layer-1 reports written under $THESIS/sessions/*/ai_usage_report.json"
