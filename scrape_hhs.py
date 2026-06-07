#!/usr/bin/env python3
"""
HHS OCR HIPAA Breach Portal — Weekly CSV Scraper
=================================================
Downloads the breach report CSV from:
  https://ocrportal.hhs.gov/ocr/breach/breach_report.jsf

Then compares it against the previous week's download to find NEW entries.

Usage:
    python scrape_hhs.py                  # Download CSV + diff against last week
    python scrape_hhs.py --baseline       # Just save current download as baseline (first run)
    python scrape_hhs.py --diff-only      # Skip download, just diff the two most recent CSVs
"""

import os
import sys
import csv
import time
import glob
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "hhs_data"          # Stores dated CSV snapshots
DOWNLOAD_DIR = SCRIPT_DIR / "hhs_downloads" # Temp dir for Selenium downloads
NEW_ENTRIES_FILE = SCRIPT_DIR / "hhs_new_entries.json"  # Output for new entries


# ─── Step 1: Download CSV via Selenium ────────────────────────────────────────

def download_csv():
    """
    Launches headless Chrome, navigates to the HHS breach portal,
    and clicks the CSV export button. Returns path to downloaded file.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print("[Error] selenium not installed. Run:")
        print("  pip install selenium webdriver-manager")
        sys.exit(1)

    try:
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        print("[Error] webdriver-manager not installed. Run:")
        print("  pip install webdriver-manager")
        sys.exit(1)

    # Prep download directory
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    for f in DOWNLOAD_DIR.glob("*"):
        f.unlink()

    print("Starting headless Chrome...")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("prefs", {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
    })

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    # Enable downloads in headless mode
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(DOWNLOAD_DIR),
    })

    try:
        url = "https://ocrportal.hhs.gov/ocr/breach/breach_report.jsf"
        print(f"Navigating to {url} ...")
        driver.get(url)

        wait = WebDriverWait(driver, 30)

        # Wait for page content to render
        print("Waiting for page to load...")
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "table, [class*='datatable'], [id*='breach']")
            ))
        except Exception:
            time.sleep(5)  # fallback static wait

        # --- Find the CSV export button ---
        # The HHS portal uses PrimeFaces; export buttons are typically
        # <a> tags with a title or text referencing "CSV".
        print("Looking for CSV export button...")
        csv_btn = None

        # Ordered list of strategies to locate the button
        strategies = [
            (By.CSS_SELECTOR, "a[title*='CSV']"),
            (By.CSS_SELECTOR, "button[title*='CSV']"),
            (By.CSS_SELECTOR, "[id*='csvButton']"),
            (By.CSS_SELECTOR, "[id*='csv']"),
            (By.CSS_SELECTOR, "[id*='CSV']"),
            (By.PARTIAL_LINK_TEXT, "CSV"),
            (By.CSS_SELECTOR, "a[title*='Export']"),
            (By.CSS_SELECTOR, "a[title*='export']"),
            (By.CSS_SELECTOR, "a[title*='Download']"),
        ]

        for by, val in strategies:
            try:
                els = driver.find_elements(by, val)
                if els:
                    csv_btn = els[0]
                    print(f"  Found via ({by}, '{val}')")
                    break
            except Exception:
                continue

        # Broader fallback — scan every link/button for export keywords
        if csv_btn is None:
            print("  Primary selectors missed. Scanning all clickable elements...")
            for el in driver.find_elements(By.CSS_SELECTOR, "a, button, input[type='submit']"):
                blob = " ".join(filter(None, [
                    el.text, el.get_attribute("title"),
                    el.get_attribute("id"), el.get_attribute("value"),
                ])).lower()
                if "csv" in blob or "export" in blob or "download" in blob:
                    csv_btn = el
                    print(f"  Found button: text='{el.text}' id='{el.get_attribute('id')}'")
                    break

        if csv_btn is None:
            debug_path = SCRIPT_DIR / "hhs_debug.html"
            debug_path.write_text(driver.page_source, encoding="utf-8")
            print(f"[Error] Could not locate CSV button. Page saved to {debug_path}")
            return None

        # Click it
        print("Clicking CSV export...")
        driver.execute_script("arguments[0].scrollIntoView(true);", csv_btn)
        time.sleep(0.5)
        driver.execute_script("arguments[0].click();", csv_btn)

        # Wait for download
        print("Waiting for download...")
        downloaded = None
        for tick in range(60):
            time.sleep(1)
            csvs = list(DOWNLOAD_DIR.glob("*.csv"))
            partials = list(DOWNLOAD_DIR.glob("*.crdownload")) + list(DOWNLOAD_DIR.glob("*.tmp"))
            if csvs and not partials:
                downloaded = csvs[0]
                break
            if tick % 10 == 9:
                print(f"  Still waiting… ({tick+1}s)")

        if downloaded:
            print(f"Download complete: {downloaded.name}  ({downloaded.stat().st_size:,} bytes)")
        else:
            all_files = list(DOWNLOAD_DIR.iterdir())
            print(f"[Error] Download timed out. Files in dir: {[f.name for f in all_files]}")

        return downloaded

    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[Error] Selenium failed: {exc}")
        return None
    finally:
        driver.quit()


# ─── Step 2: Read a CSV into a set of unique rows ────────────────────────────

def _key_for_row(row: dict) -> str:
    """
    Build a unique key for a breach row so we can compare sets.
    Uses (Name, State, Submission Date, Individuals Affected) as the composite key.
    """
    # Normalize keys — CSVs from HHS sometimes have slight header variations
    name = ""
    state = ""
    date = ""
    count = ""
    for k, v in row.items():
        kl = k.strip().lower()
        if "name" in kl and "entity" in kl:
            name = v.strip()
        elif kl == "state":
            state = v.strip()
        elif "submission" in kl or "report" in kl:
            date = v.strip()
        elif "individual" in kl:
            count = v.strip()
    return f"{name}||{state}||{date}||{count}"


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    """Returns (fieldnames, rows) from a CSV file."""
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    return fieldnames, rows


# ─── Step 3: Diff two CSVs ───────────────────────────────────────────────────

def diff_csvs(old_path: Path, new_path: Path) -> list[dict]:
    """
    Compares two HHS breach CSVs and returns rows present in new but not in old.
    """
    print(f"\nComparing:")
    print(f"  OLD: {old_path.name}")
    print(f"  NEW: {new_path.name}")

    _, old_rows = read_csv(old_path)
    _, new_rows = read_csv(new_path)

    old_keys = {_key_for_row(r) for r in old_rows}

    new_entries = []
    for r in new_rows:
        if _key_for_row(r) not in old_keys:
            new_entries.append(r)

    print(f"  Old entries: {len(old_rows)}")
    print(f"  New entries: {len(new_rows)}")
    print(f"  Δ  NEW breaches found: {len(new_entries)}")
    return new_entries


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="HHS OCR Breach CSV weekly scraper & diff")
    parser.add_argument("--baseline", action="store_true",
                        help="Save this download as the baseline (first run ever).")
    parser.add_argument("--diff-only", action="store_true",
                        help="Skip downloading; just diff the two most recent CSVs in hhs_data/.")
    parser.add_argument("--csv", type=str,
                        help="Path to a CSV you already downloaded (skip Selenium).")
    parser.add_argument("--process-all", action="store_true",
                        help="Force all entries in the CSV to be processed as new.")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    # ── Get the new CSV ──────────────────────────────────────────────────
    if args.diff_only:
        new_csv_path = None  # we'll grab from DATA_DIR below
    elif args.csv:
        src = Path(args.csv)
        if not src.exists():
            print(f"[Error] File not found: {src}")
            sys.exit(1)
        dest = DATA_DIR / f"breach_report_{today}.csv"
        shutil.copy2(src, dest)
        new_csv_path = dest
        print(f"Copied {src.name} → {dest.name}")
    else:
        raw = download_csv()
        if raw is None:
            print("[Error] Download failed. Exiting.")
            sys.exit(1)
        dest = DATA_DIR / f"breach_report_{today}.csv"
        shutil.move(str(raw), str(dest))
        new_csv_path = dest
        print(f"Saved as {dest.name}")

    # ── Process All Flag Check ───────────────────────────────────────────
    if args.process_all:
        if new_csv_path:
            _, new_rows = read_csv(new_csv_path)
            print(f"\n[Process All] Preparing all {len(new_rows)} entries as new leads...")
            with open(NEW_ENTRIES_FILE, "w") as f:
                json.dump(new_rows, f, indent=2)
            print(f"Saved all entries to: {NEW_ENTRIES_FILE.name}")
        else:
            print("[Error] Cannot use --process-all with --diff-only unless a CSV is specified.")
        return

    # ── List all snapshots sorted by date ────────────────────────────────
    snapshots = sorted(DATA_DIR.glob("breach_report_*.csv"))

    if args.baseline or len(snapshots) < 2:
        if new_csv_path:
            print(f"\nBaseline saved: {new_csv_path.name}")
            _, rows = read_csv(new_csv_path)
            print(f"  Contains {len(rows)} breach records.")
        else:
            print("No snapshots yet. Run without --diff-only first.")
        print("Run again next week to detect new breaches.")
        return

    # ── Diff the two most recent snapshots ───────────────────────────────
    old_csv = snapshots[-2]
    new_csv = snapshots[-1]

    new_entries = diff_csvs(old_csv, new_csv)

    if not new_entries:
        print("\n✓ No new breaches since last check.")
        return

    # Print them
    print(f"\n{'='*70}")
    print(f"  {len(new_entries)} NEW BREACH(ES) DETECTED")
    print(f"{'='*70}")
    for i, entry in enumerate(new_entries, 1):
        # Print key fields — handle varying column names gracefully
        cols = {k.strip().lower(): v for k, v in entry.items()}
        name = next((v for k, v in cols.items() if "name" in k and "entity" in k), "?")
        state = cols.get("state", "?")
        count = next((v for k, v in cols.items() if "individual" in k), "?")
        date = next((v for k, v in cols.items() if "submission" in k or "report" in k), "?")
        btype = next((v for k, v in cols.items() if "type" in k and "breach" in k), "?")
        location = next((v for k, v in cols.items() if "location" in k), "?")

        print(f"\n  [{i}] {name}")
        print(f"      State: {state}  |  Affected: {count}  |  Reported: {date}")
        print(f"      Type: {btype}  |  Location: {location}")

    # Save new entries as JSON for later processing
    with open(NEW_ENTRIES_FILE, "w") as f:
        json.dump(new_entries, f, indent=2)
    print(f"\nNew entries saved to: {NEW_ENTRIES_FILE.name}")


if __name__ == "__main__":
    main()
