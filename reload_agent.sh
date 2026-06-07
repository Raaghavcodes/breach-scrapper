#!/bin/bash
# Reload the breach scraper launch agent and run it
echo "Reloading launch agent with updated scrape.py..."
launchctl unload ~/Library/LaunchAgents/com.rrr.breachscraper.plist
cp /Users/raaghavwadhawan/Developer/breach-scraper/com.rrr.breachscraper.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rrr.breachscraper.plist

# Clear old logs
> /Users/raaghavwadhawan/Developer/breach-scraper/scraper_output.log
> /Users/raaghavwadhawan/Developer/breach-scraper/scraper_error.log

echo "Kicking off new run..."
launchctl kickstart -k gui/501/com.rrr.breachscraper
echo "Done. Tail the logs to watch progress:"
echo "  tail -f /Users/raaghavwadhawan/Developer/breach-scraper/scraper_output.log"
