#!/bin/bash
# ─── HHS OCR Breach Scraper — Quick Run ──────────────────────────────────────
# Usage:
#   ./run_hhs.sh              → Download new CSV + diff against last week
#   ./run_hhs.sh --baseline   → Save current download as baseline (first run)
#   ./run_hhs.sh --diff-only  → Just diff the two most recent CSVs
#   ./run_hhs.sh --csv FILE   → Import an existing CSV instead of downloading

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/venv/bin/python"

# Use venv python if available, else system python
if [ -f "$VENV" ]; then
    PYTHON="$VENV"
else
    PYTHON="python3"
fi

echo "=== HHS OCR Breach Scraper ==="
echo "Python: $PYTHON"
echo ""

$PYTHON -u "$SCRIPT_DIR/scrape_hhs.py" "$@"

# If new entries were found and saved, run the processing/ICP-scoring/enrichment pipeline
if [ -f "$SCRIPT_DIR/hhs_new_entries.json" ]; then
    echo ""
    echo "=== Running Lead Processing & ICP Qualification ==="
    $PYTHON -u "$SCRIPT_DIR/process_leads.py"
fi

