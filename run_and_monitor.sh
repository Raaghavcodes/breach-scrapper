#!/bin/bash

# Define paths
PLIST_NAME="com.rrr.breachscraper.plist"
PLIST_SRC="/Users/raaghavwadhawan/Developer/breach-scraper/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_OUT="/Users/raaghavwadhawan/Developer/breach-scraper/scraper_output.log"
LOG_ERR="/Users/raaghavwadhawan/Developer/breach-scraper/scraper_error.log"

echo "=== Reloading Breach Scraper Launch Agent ==="

# Unload the current launch agent (ignore error if not loaded)
echo "Unloading existing launch agent..."
launchctl unload "$PLIST_DEST" 2>/dev/null

# Copy plist to launch agent directory
echo "Copying latest plist to LaunchAgents..."
cp "$PLIST_SRC" "$PLIST_DEST"

# Load the launch agent
echo "Loading launch agent..."
launchctl load "$PLIST_DEST"

# Clear logs
echo "Clearing log files..."
> "$LOG_OUT"
> "$LOG_ERR"

# Kickstart/trigger the job immediately
echo "Force triggering the job (kickstart)..."
launchctl kickstart -k gui/501/com.rrr.breachscraper

echo "=== Tail logs (Ctrl+C to stop tailing) ==="
tail -f "$LOG_OUT"
