#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse
import requests
from bs4 import BeautifulSoup

# Default configuration from environment variables
API_URL = os.getenv("API_URL", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
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

def check_with_gemini(title, description, date, full_article=None):
    """
    Sends the breach content to Gemini 2.5 Flash to:
    1. Verify if it is a third-party/vendor breach.
    2. Extract rich, outreach-ready intelligence about the company and breach.
    Uses pure HTTP requests to eliminate the need for Google SDK library installations.
    """
    # Use the full article if we have it, otherwise fall back to the RSS description
    content_for_analysis = full_article if full_article else description

    if not GEMINI_API_KEY:
        print("[Warning] GEMINI_API_KEY is not set. Falling back to keyword-only filtering.")
        has_match = bool(THIRD_PARTY_KEYWORDS.search(description))
        return {
            "is_third_party": has_match,
            "explanation": "Evaluated using keyword regex (No Gemini API Key provided).",
            "company_name": "Unknown Company",
            "company_domain": "",
            "industry": "",
            "company_summary": description,
            "pain_hypothesis": "Company suffered a data breach involving a third-party vendor or partner.",
            "talking_points": []
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    prompt = f"""
    You are a senior B2B sales researcher specializing in cybersecurity vendor risk management.
    Analyze the following data breach report and extract structured intelligence for a sales outreach team.

    Breach Title: "{title}"
    Breach Date: {date}
    Breach Content:
    \"\"\"
    {content_for_analysis}
    \"\"\"

    Extract the following:
    1. Was this breach caused by or did it involve a third-party vendor, outsourced supplier, partner agency, SaaS provider, or subcontractor?
    2. The name of the PRIMARY company that suffered the data exposure (the victim, not the vendor who caused it).
    3. The official web domain of that primary company.
    4. The industry of the primary company (e.g. Telecommunications, Healthcare, Finance, Government, Retail).
    5. A factual company summary (3-4 sentences) covering: what data was exposed, approximate number of records or scale, the exact date or timeframe of the breach, the name of the third-party vendor responsible, and the root cause (e.g. misconfigured S3 bucket, stolen credentials, insecure API).
    6. A sales pain hypothesis (2-3 sentences) from the angle of a vendor-risk / third-party security platform: what specific gap does this breach expose, what is the financial or regulatory consequence for the company, and why is now the right time to engage them?
    7. Three specific talking points (short bullet phrases) a sales rep can use when cold-calling or emailing this company — referencing the actual breach details, the vendor name, and the business impact.

    Return a JSON response with:
    - is_third_party_breach (boolean)
    - explanation (string): one sentence on why it is or is not a third-party breach.
    - company_name (string): name of the compromised primary company.
    - company_domain (string): official domain of the primary company.
    - industry (string): industry of the primary company.
    - company_summary (string): rich 3-4 sentence factual breach summary including date, vendor name, scale, and root cause.
    - pain_hypothesis (string): 2-3 sentence sales pain hypothesis with regulatory and financial context.
    - talking_points (array of strings): exactly 3 short, specific talking points for a cold outreach.
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "is_third_party_breach": {"type": "BOOLEAN"},
                    "explanation": {"type": "STRING"},
                    "company_name": {"type": "STRING"},
                    "company_domain": {"type": "STRING"},
                    "industry": {"type": "STRING"},
                    "company_summary": {"type": "STRING"},
                    "pain_hypothesis": {"type": "STRING"},
                    "talking_points": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"}
                    }
                },
                "required": ["is_third_party_breach", "explanation", "company_name", "company_domain", "industry", "company_summary", "pain_hypothesis", "talking_points"]
            }
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=30)
        res.raise_for_status()
        data = res.json()
        text_content = data["candidates"][0]["content"]["parts"][0]["text"]
        result = json.loads(text_content)
        return {
            "is_third_party": result.get("is_third_party_breach", False),
            "explanation": result.get("explanation", "Parsed via Gemini API."),
            "company_name": result.get("company_name", "Unknown Company"),
            "company_domain": result.get("company_domain", ""),
            "industry": result.get("industry", ""),
            "company_summary": result.get("company_summary", description),
            "pain_hypothesis": result.get("pain_hypothesis", "Company suffered a data breach involving a third-party vendor or partner."),
            "talking_points": result.get("talking_points", [])
        }
    except Exception as e:
        print(f"[Error] Gemini API validation failed: {e}. Falling back to regex keyword logic.")
        return {
            "is_third_party": bool(THIRD_PARTY_KEYWORDS.search(description)),
            "explanation": "Gemini error fallback. Match detected via regex.",
            "company_name": "Unknown Company",
            "company_domain": "",
            "industry": "",
            "company_summary": description,
            "pain_hypothesis": "Company suffered a data breach involving a third-party vendor or partner.",
            "talking_points": []
        }

def fetch_full_article(url):
    """
    Follows a breach article URL and scrapes the full body text for richer context.
    Falls back gracefully if the page is blocked or unscrapable.
    """
    if not url:
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        # Remove navigation, headers, footers, scripts, and ads
        for tag in soup.find_all(["nav", "header", "footer", "script", "style", "noscript", "aside"]):
            tag.decompose()

        # Try to find the main article content
        article = (
            soup.find("article") or
            soup.find("main") or
            soup.find("div", class_=lambda c: c and any(x in c for x in ["content", "post", "article", "body"]))
        )
        target = article if article else soup.find("body")

        if not target:
            return None

        # Extract all paragraph text, join with newlines
        paragraphs = [p.get_text(strip=True) for p in target.find_all("p") if len(p.get_text(strip=True)) > 40]
        full_text = "\n".join(paragraphs)

        # Cap to ~3000 chars to keep Gemini tokens reasonable
        return full_text[:3000] if full_text else None
    except Exception as e:
        print(f"  [Warning] Could not fetch full article from {url}: {e}")
        return None


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
    Scrapes fresh breach data from https://databreach.com/breach
    Uses curl_cffi to impersonate a real browser's TLS fingerprint,
    which bypasses Cloudflare's bot detection.
    """
    target_url = "https://databreach.com/breach"
    print(f"Scraping breaches from {target_url}...")

    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        print("[Error] curl_cffi is not installed. Install it with: pip install curl-cffi")
        print("  Falling back to standard requests (may be blocked by Cloudflare)...")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            }
            res = requests.get(target_url, headers=headers, timeout=15)
            res.raise_for_status()
            html = res.text
        except Exception as e:
            print(f"[Error] Failed to request {target_url}: {e}")
            return []
    else:
        try:
            res = cffi_requests.get(target_url, impersonate="chrome124", timeout=15)
            res.raise_for_status()
            html = res.text
            # Dump the page HTML to a file so we can inspect the elements
            with open("/Users/raaghavwadhawan/Developer/breach-scraper/page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("  [Debug] Saved page HTML to page.html")
        except Exception as e:
            print(f"[Error] Failed to request {target_url} via curl_cffi: {e}")
            return []

    soup = BeautifulSoup(html, "html.parser")

    breach_items = []

    # databreach.com lists breaches in rows/cards — try multiple selector strategies
    # Strategy 1: Look for table rows or list items with breach data
    rows = soup.select("table tbody tr") or soup.select(".breach-list .breach-item") or soup.select("[class*='breach'] [class*='row']")

    if rows:
        print(f"  Found {len(rows)} breach entries via table/list selectors.")
        for row in rows:
            try:
                cells = row.find_all("td") if row.find("td") else row.find_all("div")
                if not cells or len(cells) < 2:
                    continue

                # Extract what we can from the row — names, dates, row counts
                text_parts = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
                if not text_parts:
                    continue

                # First meaningful text is usually the company name
                title = text_parts[0]

                # Try to find a link
                link = row.find("a")
                url = link["href"] if link and link.get("href") else ""
                if url and not url.startswith("http"):
                    url = f"https://databreach.com{url}"

                # Try to extract date and record count from remaining cells
                date = ""
                row_count = ""
                description_parts = [f"Company: {title}."]

                for part in text_parts[1:]:
                    if re.search(r"\d{4}-\d{2}-\d{2}", part):
                        if not date:
                            date = part
                            description_parts.append(f"Est. Breach Date: {part}.")
                        else:
                            description_parts.append(f"Added: {part}.")
                    elif re.search(r"\d+[kKmM]?\s*rows?", part, re.IGNORECASE):
                        row_count = part
                        description_parts.append(f"Records exposed: {part}.")
                    else:
                        description_parts.append(part)

                description = " ".join(description_parts)

                breach_items.append({
                    "title": title,
                    "url": url,
                    "date": date if date else "Recent",
                    "description": description,
                    "domain": "",
                    "row_count": row_count
                })
            except Exception as e:
                print(f"  [Warning] Failed to parse a row: {e}")
                continue
    else:
        # Strategy 2: Fall back to finding any structured breach data on the page
        print("  No table/list rows found. Trying generic element extraction...")

        # Look for any elements that look like breach cards
        cards = soup.find_all(["div", "article", "li"], class_=lambda c: c and any(
            x in str(c).lower() for x in ["breach", "company", "entry", "item", "card", "row"]
        ))

        if not cards:
            # Last resort — try to find all links with breach-like paths
            cards = soup.find_all("a", href=lambda h: h and "/breach/" in h)

        print(f"  Found {len(cards)} potential breach elements.")

        for card in cards:
            try:
                text = card.get_text(separator=" | ", strip=True)
                if len(text) < 5:
                    continue

                # Try to extract company name (usually the first/most prominent text)
                title_el = card.find(["h2", "h3", "h4", "strong", "b", "span"])
                title = title_el.get_text(strip=True) if title_el else text.split("|")[0].strip()

                link = card.find("a") if card.name != "a" else card
                url = ""
                if link and link.get("href"):
                    url = link["href"]
                    if not url.startswith("http"):
                        url = f"https://databreach.com{url}"

                # Extract dates
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
                date = date_match.group(0) if date_match else "Recent"

                breach_items.append({
                    "title": title,
                    "url": url,
                    "date": date,
                    "description": text,
                    "domain": "",
                    "row_count": ""
                })
            except Exception as e:
                print(f"  [Warning] Failed to parse an element: {e}")
                continue

    # If we still got nothing, dump the page structure for debugging
    if not breach_items:
        print(f"  [Debug] Page title: {soup.title.string if soup.title else 'N/A'}")
        # Check if we got a Cloudflare challenge page
        if "cf-" in html.lower() or "cloudflare" in html.lower() or "challenge" in html.lower():
            print("  [Error] Got a Cloudflare challenge page. curl_cffi may need updating.")
        else:
            print("  [Debug] Page has content but selectors didn't match. First 500 chars:")
            print(f"  {html[:500]}")

    print(f"  Total breaches extracted: {len(breach_items)}")
    return breach_items

def main():
    parser = argparse.ArgumentParser(description="Scrape HIBP for vendor-risk cold leads.")
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
        
        # 1. Regex Pre-Filter check on the breach description
        if not THIRD_PARTY_KEYWORDS.search(desc):
            print("  [Skip] No third-party keywords matched.")
            continue
            
        print("  [Match] Pre-filter matches. Sending to Gemini for verification and enrichment...")

        # 2. AI validation + enrichment (name, domain, summary, pain hypothesis)
        # HIBP descriptions are already rich with dates, record counts, and data classes
        # so we don't need to fetch a separate article
        assessment = check_with_gemini(item["title"], desc, item.get("date", "Unknown"))

        if assessment["is_third_party"]:
            print(f"  [Qualified] {assessment['explanation']}")
            
            # Use name from Gemini if available, fallback to title cleanup
            name = assessment.get("company_name")
            if not name or name == "Unknown Company":
                name = item["title"].split(":")[0].strip()
                if "|" in name:
                    name = name.split("|")[0].strip()
                name = name.replace("Data Leak", "").replace("Leak", "").replace("Breach", "").strip(" .-,")
            
            # Prefer the domain from HIBP directly (it's structured), fall back to Gemini
            domain = item.get("domain") or assessment.get("company_domain", "")
            
            talking_points = assessment.get("talking_points", [])
            talking_points_text = "\n".join(f"\u2022 {pt}" for pt in talking_points) if talking_points else ""

            # Build a rich painHypothesis that includes talking points
            pain_with_points = assessment.get("pain_hypothesis", "")
            if talking_points_text:
                pain_with_points += f"\n\nOutreach Talking Points:\n{talking_points_text}"

            qualified_companies.append({
                "name": name,
                "domain": domain,
                "website": f"https://{domain}" if domain else "",
                "industry": assessment.get("industry", ""),
                "companySummary": assessment.get("company_summary", desc),
                "painHypothesis": pain_with_points
            })
        else:
            print(f"  [Disqualified] {assessment['explanation']}")

    # 3. Post to web application ingestion endpoint
    post_to_lead_gen(qualified_companies)
    print("\n=== Pipeline Execution Finished ===")

if __name__ == "__main__":
    main()
