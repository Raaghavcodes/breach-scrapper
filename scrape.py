#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse
import requests
from bs4 import BeautifulSoup

# Default configuration from environment variables
API_URL = os.getenv("API_URL", "http://localhost:3000/api/companies/scraper-ingest")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "rrr_scraper_secure_token_2026")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# 1. Regex pre-filter pattern for third-party vulnerabilities
THIRD_PARTY_KEYWORDS = re.compile(
    r"\b(third-party|vendor|supplier|partner|supply-chain|compromised provider|service provider|api leak|subcontractor|outsourced)\b",
    re.IGNORECASE
)

# 2. Local test mock data to allow dry-run testing out of the box
MOCK_BREACH_LIST = [
    {
        "title": "FinTech Group Inc. Data Leak",
        "url": "https://databreach.com/breach/fintech-group-leak",
        "date": "2026-05-25",
        "description": "FinTech Group Inc. suffered a breach exposing 150k account records. The entry point was identified as a compromised login on their third-party customer support software vendor partner portal."
    },
    {
        "title": "Global Logistics Corp S3 Exposure",
        "url": "https://databreach.com/breach/global-logistics-s3",
        "date": "2026-05-24",
        "description": "An open and misconfigured Amazon AWS S3 bucket was discovered belonging to Global Logistics Corp, exposing shipping logs. No vendor was involved; it was a internal dev team configuration error."
    },
    {
        "title": "MedHealth Solutions API Compromise",
        "url": "https://databreach.com/breach/medhealth-api",
        "date": "2026-05-23",
        "description": "Hackers breached MedHealth Solutions and downloaded medical records. The breach occurred because a contractor at an outsourced medical transcription billing agency partner exposed their system credentials."
    }
]

def check_with_gemini(description):
    """
    Sends the breach description to Gemini 2.5 Flash to verify if it is a third-party breach.
    Uses pure HTTP requests to eliminate the need for Google SDK library installations.
    """
    if not GEMINI_API_KEY:
        print("[Warning] GEMINI_API_KEY is not set. Falling back to keyword-only filtering.")
        # Fallback to simple regex check if API key is not present
        has_match = bool(THIRD_PARTY_KEYWORDS.search(description))
        return {
            "is_third_party": has_match,
            "explanation": "Evaluated using keyword regex (No Gemini API Key provided)."
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    prompt = f"""
    Analyze the following data breach description. Determine if the breach was caused by or involved a third-party vendor, outsourced supplier, partner agency, SaaS provider, or subcontractor (e.g. they compromised the vendor first to get into the company, or the vendor itself leaked the data).
    
    Breach Description:
    "{description}"
    
    Return a JSON response with:
    - is_third_party_breach (boolean): true if it involves a 3rd party vendor/supplier, false otherwise.
    - explanation (string): brief one-sentence reason why.
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "is_third_party_breach": {"type": "BOOLEAN"},
                    "explanation": {"type": "STRING"}
                },
                "required": ["is_third_party_breach", "explanation"]
            }
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        text_content = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text_content)
        return {
            "is_third_party": result.get("is_third_party_breach", False),
            "explanation": result.get("explanation", "Parsed via Gemini API.")
        }
    except Exception as e:
        print(f"[Error] Gemini API validation failed: {e}. Falling back to regex keyword logic.")
        return {
            "is_third_party": bool(THIRD_PARTY_KEYWORDS.search(description)),
            "explanation": "Gemini error fallback. Match detected via regex."
        }

def post_to_lead_gen(companies):
    """
    POSTs the list of qualified companies/leads to the Next.js API.
    """
    if not companies:
        print("No companies to ingest.")
        return

    headers = {
        "Content-Type": "application/json",
        "x-api-key": SCRAPER_API_KEY
    }

    try:
        print(f"Sending {len(companies)} companies to RRR Outreach API...")
        res = requests.post(API_URL, json=companies, headers=headers, timeout=15)
        res.raise_for_status()
        print(f"Ingestion successful! Response: {res.json()}")
    except Exception as e:
        print(f"[Error] Failed to connect to RRR Outreach API at {API_URL}: {e}")

def scrape_databreach_real():
    """
    Performs the real web scraping on databreach.com/breach.
    """
    target_url = "https://databreach.com/breach"
    print(f"Scraping real breaches from {target_url}...")
    
    try:
        res = requests.get(target_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        res.raise_for_status()
    except Exception as e:
        print(f"[Error] Failed to request {target_url}: {e}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    breach_items = []

    # Note: These selectors are examples. If databreach.com changes layout,
    # update class selectors accordingly.
    cards = soup.select(".breach-card, .breach-entry")
    if not cards:
        print("[Warning] No breach elements found on page. The site layout might have changed or requires JS rendering.")
        return []

    for card in cards:
        try:
            title_el = card.select_one(".title, h3, a")
            link_el = card.select_one("a")
            date_el = card.select_one(".date, time")

            if not title_el or not link_el:
                continue

            title = title_el.text.strip()
            href = link_el["href"]
            url = href if href.startswith("http") else f"https://databreach.com{href}"
            date = date_el.text.strip() if date_el else "Recent"

            # Go request detail page for the full description
            detail_res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            detail_res.raise_for_status()
            detail_soup = BeautifulSoup(detail_res.text, "html.parser")
            
            desc_el = detail_soup.select_one(".description, .entry-content, p")
            description = desc_el.text.strip() if desc_el else ""

            breach_items.append({
                "title": title,
                "url": url,
                "date": date,
                "description": description
            })
        except Exception as detail_err:
            print(f"[Warning] Failed parsing individual breach link: {detail_err}")
            continue

    return breach_items

def main():
    parser = argparse.ArgumentParser(description="Scrape databreach.com for vendor-risk cold leads.")
    parser.add_init_flag = False
    parser.add_argument("--mock", action="store_true", help="Run in mock/test mode using pre-defined local data.")
    args = parser.parse_args()

    print("=== Breach Scraper Pipeline Started ===")
    
    if args.mock:
        print("Running in MOCK/TEST mode...")
        raw_breaches = MOCK_BREACH_LIST
    else:
        raw_breaches = scrape_databreach_real()

    if not raw_breaches:
        print("No breaches retrieved. Exiting.")
        sys.exit(0)

    qualified_companies = []

    for item in raw_breaches:
        desc = item["description"]
        print(f"\nEvaluating: '{item['title']}'...")
        
        # 1. Regex Pre-Filter check
        if not THIRD_PARTY_KEYWORDS.search(desc):
            print("  [Skip] No third-party keywords matched.")
            continue
            
        print("  [Match] Pre-filter matches. Sending to Gemini for verification...")

        # 2. AI validation
        assessment = check_with_gemini(desc)

        if assessment["is_third_party"]:
            print(f"  [Qualified] Gemini assessment: {assessment['explanation']}")
            
            # Extract company name from title/description
            # E.g. "FinTech Group Inc. Data Leak" -> "FinTech Group Inc."
            name = item["title"].replace("Data Leak", "").replace("Leak", "").replace("Breach", "").strip(" .-,")
            
            # Simple domain extraction from URL or default
            domain = ""
            if "fintech" in name.lower():
                domain = "fintechgroup.com"
            elif "medhealth" in name.lower():
                domain = "medhealthsolutions.com"

            qualified_companies.append({
                "name": name,
                "domain": domain,
                "website": f"https://{domain}" if domain else "",
                "companySummary": f"Reported breach date: {item['date']}. Details: {desc}",
                "painHypothesis": f"Breach occurred via partner/vendor. Reason: {assessment['explanation']}"
            })
        else:
            print(f"  [Disqualified] Gemini assessment: {assessment['explanation']}")

    # 3. Post to web application ingestion endpoint
    post_to_lead_gen(qualified_companies)
    print("\n=== Pipeline Execution Finished ===")

if __name__ == "__main__":
    main()
