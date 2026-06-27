#!/bin/bash
# clean_logs.sh - free disk space by emptying large osquery/stream logs.
# macOS has no `truncate`; we use `: > file` redirection (run as root) instead,
# which empties a file in place while the daemon keeps its open handle.
#
# Does NOT touch session data, recordings, or the participant index.
# Run between sessions if the disk is getting tight.
#
#   bash clean_logs.sh            # show sizes, then empty the global osquery log
#   bash clean_logs.sh --all      # also empty stale per-stream .log files in sessions/

THESIS_DIR="${THESIS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)}"
GLOBAL_LOG="$THESIS_DIR/osquery_logs/osqueryd.results.log"

echo "Disk before:"
df -h / | tail -1
echo ""

echo "osquery global log:"
sudo ls -lh "$GLOBAL_LOG" 2>/dev/null || echo "  (not found)"
echo ""

# IMPORTANT: if an aborted session still needs its osquery.jsonl, run
# reslice_osquery.py on that session BEFORE emptying this log.
printf "Empty the osquery global log now? [y/N] "
read -r ans
if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    sudo sh -c ": > '$GLOBAL_LOG'"
    echo "  Emptied. Daemon keeps writing to the now-empty file."
else
    echo "  Skipped."
fi
echo ""

if [ "$1" = "--all" ]; then
    echo "Per-stream .log files larger than 50 MB in sessions/:"
    find "$THESIS_DIR/sessions" -name "*.log" -size +50M 2>/dev/null -exec ls -lh {} \;
    printf "Empty those oversized .log files? (keeps .jsonl data) [y/N] "
    read -r ans2
    if [ "$ans2" = "y" ] || [ "$ans2" = "Y" ]; then
        find "$THESIS_DIR/sessions" -name "*.log" -size +50M 2>/dev/null \
            -exec sh -c ': > "$1"' _ {} \;
        echo "  Emptied oversized stream logs."
    else
        echo "  Skipped."
    fi
    echo ""
fi

echo "Disk after:"
df -h / | tail -1
