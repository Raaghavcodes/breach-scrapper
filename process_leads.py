#!/usr/bin/env python3
"""
HHS OCR Breach — Qualification & Enrichment Pipeline
=====================================================
Processes newly detected healthcare breaches:
1. Validates and scores them against RRR.dev's ICP (Ideal Customer Profile) using Gemini.
2. Ingests qualified companies into the database.
3. Runs Explorium enrichment to find key decision-makers (CISO, GRC, IT Security)
   and saves them as contacts in the CRM.

This script runs either automatically after scrape_hhs.py, or manually.
"""

import os
import sys
import json
import time
import requests
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
NEW_ENTRIES_FILE = SCRIPT_DIR / "hhs_new_entries.json"

# Read environment variables
API_URL = os.getenv("API_URL", "")
CONTACTS_API_URL = os.getenv("CONTACTS_API_URL", "")
SCRAPER_API_KEY = os.getenv("SCRAPER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EXPLORIUM_API_KEY = os.getenv("EXPLORIUM_API_KEY", "")

# ICP thresholds
MIN_QUALIFY_FIT_SCORE = 60.0

# ─── Helper Functions ─────────────────────────────────────────────────────────

def normalize_entry_keys(entry: dict) -> dict:
    """
    Normalizes inconsistent or JSF-specific headers in the OCR CSV to standard keys.
    """
    normalized = {}
    for k, v in entry.items():
        if not k or not v:
            continue
        kl = k.strip().lower()
        vl = v.strip()
        
        # Name column (javax.faces.component.UIPanel@2cf240e or similar, or Name of Covered Entity)
        if ("name" in kl and "entity" in kl) or (kl.startswith("javax.faces.component.uipanel") and not kl.endswith("5ebd4c4")):
            normalized["name"] = vl
        elif kl == "state":
            normalized["state"] = vl
        elif "covered entity type" in kl or "entity type" in kl:
            normalized["entity_type"] = vl
        elif "individuals affected" in kl or "individuals" in kl:
            normalized["individuals_affected"] = vl.replace(",", "")
        elif "submission" in kl or "date" in kl:
            normalized["submission_date"] = vl
        elif "type of breach" in kl or "breach type" in kl:
            normalized["breach_type"] = vl
        elif "location" in kl:
            normalized["location"] = vl
        elif "ba_present" in kl or "business associate" in kl or kl.endswith("5ebd4c4"):
            normalized["ba_present"] = vl
        elif "web description" in kl or "description" in kl:
            normalized["description"] = vl
            
    # Fallback if name was not matched
    if "name" not in normalized and entry:
        first_key = list(entry.keys())[0]
        normalized["name"] = entry[first_key].strip() if entry[first_key] else "Unknown Entity"
        
    # Default other fields
    normalized.setdefault("state", "US")
    normalized.setdefault("entity_type", "Healthcare Provider")
    normalized.setdefault("individuals_affected", "500")
    normalized.setdefault("submission_date", "")
    normalized.setdefault("breach_type", "Hacking/IT Incident")
    normalized.setdefault("location", "Network Server")
    normalized.setdefault("ba_present", "No")
    normalized.setdefault("description", "")
    
    return normalized


def get_company_firmographics(company_name: str, domain: str, individuals_affected: int) -> dict:
    """
    Looks up company firmographics (employeeCount, industry) from Explorium.
    If EXPLORIUM_API_KEY is missing, falls back to a high-fidelity simulator.
    """
    if not domain or domain in ["not_provided.com", "unknown.com", "n/a"]:
        return {"employeeCount": None, "industry": "Healthcare"}

    if not EXPLORIUM_API_KEY:
        # High fidelity simulation of company size
        # Estimate size loosely from individuals affected to make the mock data look realistic
        if individuals_affected > 500000:
            emp_count = 15000  # Enterprise Health Network
            industry = "Hospitals and Health Care"
        elif individuals_affected > 50000:
            emp_count = 3500   # Large Health System
            industry = "Hospitals and Health Care"
        elif individuals_affected > 10000:
            emp_count = 850    # Mid-market Medical Group
            industry = "Medical Practices"
        elif individuals_affected > 2000:
            emp_count = 250    # Regional Health Center
            industry = "Medical Practices"
        else:
            emp_count = 65     # Small Clinic / Specialized Office
            industry = "Medical Practices"
        return {"employeeCount": emp_count, "industry": industry}

    # Real Explorium lookup
    headers = {
        "api_key": EXPLORIUM_API_KEY,
        "Content-Type": "application/json"
    }
    match_url = "https://api.explorium.ai/v1/businesses/match"
    match_payload = {
        "company_domain": domain,
        "company_name": company_name
    }
    try:
        res = requests.post(match_url, json=match_payload, headers=headers, timeout=15)
        res.raise_for_status()
        data = res.json()
        
        # Extract employee count and industry
        emp_count = data.get("employee_count")
        industry = data.get("industry", "Healthcare")
        return {
            "employeeCount": int(emp_count) if emp_count else None,
            "industry": industry
        }
    except Exception as e:
        print(f"    [Error] Explorium firmographics check failed: {e}")
        return {"employeeCount": None, "industry": "Healthcare"}


# ─── Step 1: Gemini ICP Analysis & Scoring ────────────────────────────────────

def analyze_company_with_gemini(entry: dict) -> dict:
    """
    Sends the raw breach details to Gemini to score the company's fit for RRR.dev,
    resolve its domain, and outline a custom vendor risk/DLP pain hypothesis.
    Includes rate limit retry logic.
    """
    if not GEMINI_API_KEY:
        print("[Warning] GEMINI_API_KEY is not set. Using default fallback scoring.")
        return {
            "name": entry.get("name", "Unknown Entity"),
            "domain": "",
            "website": "",
            "fitScore": 50.0,
            "fitReason": "Fallback: Default score applied due to missing Gemini API key.",
            "aiAdoptionScore": 50.0,
            "securityMaturityScore": 40.0,
            "shadowAiRiskScore": 50.0,
            "companySummary": entry.get("description", ""),
            "painHypothesis": f"Compromised entity requires better DLP and vendor risk management.",
            "industry": "Healthcare"
        }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    prompt = f"""
    You are a senior B2B SaaS Growth Analyst at RRR.dev.
    RRR.dev helps mid-market and enterprise organizations secure and manage Shadow AI, SaaS tools, 
    third-party vendors (vendor risk), and Data Loss Prevention (DLP) for compliance.

    Evaluate the following healthcare breach reported to the HHS OCR:
    - Entity Name: {entry.get('name')}
    - State: {entry.get('state')}
    - Entity Type: {entry.get('entity_type')}
    - Individuals Affected: {entry.get('individuals_affected')}
    - Type of Breach: {entry.get('breach_type')}
    - Location of Breached Info: {entry.get('location')}
    - Business Associate Present (vendor involved): {entry.get('ba_present')}

    Your goals:
    1. Resolve the official corporate/organizational website domain for this entity (e.g. for "Deaconess Health System" it is "deaconess.com").
    2. Determine if this company matches RRR.dev's ICP (ideal size: 100-5000 employees, sensitive to security, privacy, compliance).
    3. Score the fit (0-100) based on size, exposure, and potential vulnerability to vendor/SaaS issues.
    4. Estimate scores:
       - aiAdoptionScore (0-100): likelihood of SaaS/AI usage based on organization type
       - shadowAiRiskScore (0-100): risk of employees using unsanctioned tools
       - securityMaturityScore (0-100): current estimated posture
    5. Draft a detailed 'fitReason' explaining why we should (or shouldn't) target them.
    6. Draft a tailored 'painHypothesis' highlighting the risk of OCR HIPAA fines, class action lawsuits, and how RRR.dev's vendor risk/SaaS governance can prevent future incidents.

    Return JSON matching this schema:
    {{
      "company_name": "string (entity name)",
      "company_domain": "string (official domain, e.g. deaconess.com. DO NOT include www or https)",
      "fit_score": number (0 to 100),
      "fit_reason": "string (2-3 sentences explaining the ICP match)",
      "ai_adoption_score": number (0 to 100),
      "shadow_ai_risk_score": number (0 to 100),
      "security_maturity_score": number (0 to 100),
      "company_summary": "string (factual summary of the breach and scale)",
      "pain_hypothesis": "string (outreach pain narrative highlighting HIPAA, OCR audits, and vendor oversight)"
    }}
    """

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                  "company_name": {"type": "STRING"},
                  "company_domain": {"type": "STRING"},
                  "fit_score": {"type": "NUMBER"},
                  "fit_reason": {"type": "STRING"},
                  "ai_adoption_score": {"type": "NUMBER"},
                  "shadow_ai_risk_score": {"type": "NUMBER"},
                  "security_maturity_score": {"type": "NUMBER"},
                  "company_summary": {"type": "STRING"},
                  "pain_hypothesis": {"type": "STRING"}
                },
                "required": [
                    "company_name", "company_domain", "fit_score", "fit_reason", 
                    "ai_adoption_score", "shadow_ai_risk_score", "security_maturity_score", 
                    "company_summary", "pain_hypothesis"
                ]
            }
        }
    }

    # Attempt request with exponential backoff on 429 (rate limiting)
    max_attempts = 5
    for attempt in range(max_attempts):
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=20)
            if res.status_code == 429:
                wait_time = (2 ** attempt) + 3
                print(f"    [Rate Limit] Gemini 429 received. Retrying in {wait_time} seconds (attempt {attempt+1}/{max_attempts})...")
                time.sleep(wait_time)
                continue
            res.raise_for_status()
            result = json.loads(res.json()["candidates"][0]["content"]["parts"][0]["text"])

            # Map to database keys
            return {
                "name": result.get("company_name", entry.get("name")),
                "domain": result.get("company_domain", "").strip().lower(),
                "website": f"https://{result.get('company_domain')}" if result.get("company_domain") else "",
                "fitScore": float(result.get("fit_score", 50)),
                "fitReason": result.get("fit_reason", ""),
                "aiAdoptionScore": float(result.get("ai_adoption_score", 50)),
                "securityMaturityScore": float(result.get("security_maturity_score", 50)),
                "shadowAiRiskScore": float(result.get("shadow_ai_risk_score", 50)),
                "companySummary": result.get("company_summary", entry.get("description")),
                "painHypothesis": result.get("pain_hypothesis", ""),
                "industry": "Healthcare",
                "status": "QUALIFIED" if float(result.get("fit_score", 50)) >= MIN_QUALIFY_FIT_SCORE else "DISQUALIFIED"
            }
        except Exception as e:
            if attempt == max_attempts - 1:
                print(f"  [Error] Gemini scoring failed after {max_attempts} attempts: {e}")
            else:
                time.sleep(1)

    # Fallback return
    return {
        "name": entry.get("name"),
        "domain": "",
        "website": "",
        "fitScore": 50.0,
        "fitReason": "Failed to generate ICP score via Gemini (API Error). Defaults applied.",
        "aiAdoptionScore": 50.0,
        "securityMaturityScore": 50.0,
        "shadowAiRiskScore": 50.0,
        "companySummary": entry.get("description", ""),
        "painHypothesis": "HIPAA breach reported. Compliance & vendor audit recommended.",
        "industry": "Healthcare",
        "status": "NEW"
    }


# ─── Step 2: Explorium Enrichment (Mock/Real Client) ───────────────────────

def enrich_leads_via_explorium(company_id: str, company_name: str, domain: str) -> list[dict]:
    """
    Queries Explorium to find security/IT/GRC decision makers for the target company.
    If EXPLORIUM_API_KEY is missing, falls back to high-fidelity mock simulation.
    """
    if not domain:
        print(f"  [Skip Enrichment] No domain available for {company_name}")
        return []

    if not EXPLORIUM_API_KEY:
        print(f"  [Enrichment Simulation] Simulating contact search for {domain}...")
        # High fidelity simulated contacts matching RRR's target buyer personas
        simulated_contacts = [
            {
                "firstName": "Sarah",
                "lastName": "Jenkins",
                "fullName": "Sarah Jenkins",
                "title": "Chief Information Security Officer",
                "department": "Security",
                "seniority": "C-Level",
                "email": f"s.jenkins@{domain}",
                "linkedinUrl": f"https://www.linkedin.com/in/sarah-jenkins-{domain.split('.')[0]}",
                "buyerPersona": "Security Executive",
                "source": "explorium_simulated"
            },
            {
                "firstName": "David",
                "lastName": "Miller",
                "fullName": "David Miller",
                "title": "Director of Governance, Risk & Compliance (GRC)",
                "department": "Compliance",
                "seniority": "Director",
                "email": f"d.miller@{domain}",
                "linkedinUrl": f"https://www.linkedin.com/in/david-miller-{domain.split('.')[0]}",
                "buyerPersona": "Governance / Compliance",
                "source": "explorium_simulated"
            }
        ]
        return simulated_contacts

    print(f"  Querying Explorium API for {company_name} ({domain})...")
    headers = {
        "api_key": EXPLORIUM_API_KEY,
        "Content-Type": "application/json"
    }

    # 1. Match company domain to retrieve Explorium Business ID
    try:
        match_url = "https://api.explorium.ai/v1/businesses/match"
        match_payload = {
            "company_domain": domain,
            "company_name": company_name
        }
        res = requests.post(match_url, json=match_payload, headers=headers, timeout=15)
        res.raise_for_status()
        match_data = res.json()
        
        business_id = match_data.get("business_id")
        if not business_id:
            print(f"    Could not match business in Explorium directory.")
            return []
        
        # 2. Fetch prospects (contacts) in relevant departments
        prospects_url = "https://api.explorium.ai/v1/prospects"
        prospects_payload = {
            "filters": {
                "business_id": business_id,
                "job_department": "information technology"  # Explorium standard department name
            }
        }
        res = requests.post(prospects_url, json=prospects_payload, headers=headers, timeout=15)
        res.raise_for_status()
        prospect_list = res.json().get("prospects", [])

        target_contacts = []
        
        # Filter for security-focused titles or leaders
        security_titles = ["ciso", "security", "grc", "compliance", "privacy", "governance", "chief information", "cto"]
        
        for p in prospect_list:
            title = (p.get("job_title") or "").lower()
            if any(t in title for t in security_titles):
                prospect_id = p.get("prospect_id")
                if not prospect_id:
                    continue

                # 3. Enrich contact details
                enrich_url = "https://api.explorium.ai/v1/prospects/contacts_information/enrich"
                enrich_payload = {"prospect_id": prospect_id}
                
                try:
                    eres = requests.post(enrich_url, json=enrich_payload, headers=headers, timeout=15)
                    eres.raise_for_status()
                    c_info = eres.json()

                    email = c_info.get("email")
                    if email:
                        # Determine persona bucket
                        persona = "IT / Infrastructure"
                        if "security" in title or "ciso" in title:
                            persona = "Security Executive"
                        elif "compliance" in title or "grc" in title:
                            persona = "Governance / Compliance"

                        target_contacts.append({
                            "firstName": p.get("first_name", ""),
                            "lastName": p.get("last_name", ""),
                            "fullName": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                            "title": p.get("job_title", ""),
                            "department": p.get("job_department", ""),
                            "seniority": p.get("seniority_level", ""),
                            "email": email,
                            "linkedinUrl": p.get("linkedin_url", ""),
                            "buyerPersona": persona,
                            "source": "explorium"
                        })
                except Exception as ex:
                    print(f"    Failed to enrich prospect {prospect_id}: {ex}")
                    continue

        return target_contacts

    except Exception as e:
        print(f"    Explorium request error: {e}")
        return []


# ─── Pipeline Execution ───────────────────────────────────────────────────────

def main():
    if not GEMINI_API_KEY or not API_URL:
        print("[Error] Missing required environment configuration (GEMINI_API_KEY, API_URL).")
        print("Please export these environment variables before running the processing pipeline.")
        return

    if not NEW_ENTRIES_FILE.exists():
        print(f"No new entries file found at {NEW_ENTRIES_FILE.name}. Run scrape_hhs.py first.")
        return

    print("=" * 60)
    print("  RRR.dev Breach Processing, Scoring & Enrichment")
    print("=" * 60)

    # 1. Load newly detected breaches
    with open(NEW_ENTRIES_FILE, "r") as f:
        try:
            new_entries = json.load(f)
        except Exception as e:
            print(f"Error loading {NEW_ENTRIES_FILE.name}: {e}")
            return

    if not new_entries:
        print("hhs_new_entries.json is empty. Nothing to process.")
        try:
            NEW_ENTRIES_FILE.unlink()
        except:
            pass
        return

    total_leads = len(new_entries)
    print(f"Found {total_leads} new breach entries to process.\n")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": SCRAPER_API_KEY
    }

    # 2. Analyze & Qualify each breach one-by-one (Incremental)
    # We loop while there are entries, popping from the list and updating the file
    processed_count = 0
    
    while new_entries:
        raw_entry = new_entries[0] # Always look at the first item
        processed_count += 1
        
        # Normalize the keys (fixes the 'None' bug from JSF headers)
        entry = normalize_entry_keys(raw_entry)
        
        print(f"[{processed_count}/{total_leads}] Processing: '{entry.get('name')}'...")
        
        # A. Resolve domain & draft pain narrative via Gemini
        analysis = analyze_company_with_gemini(entry)
        domain = analysis.get("domain", "")
        
        # B. Get actual company size & industry from Explorium
        individuals_affected = 500
        try:
            individuals_affected = int(entry.get("individuals_affected", 500))
        except ValueError:
            pass

        firmographics = get_company_firmographics(entry["name"], domain, individuals_affected)
        employee_count = firmographics.get("employeeCount")
        industry = firmographics.get("industry", "Healthcare")
        
        # C. Programmatically Qualify/Disqualify based on actual size
        fit_score = 50.0
        status = "NEW"
        fit_reason = ""
        
        if employee_count is not None:
            if 100 <= employee_count <= 5000:
                fit_score = 85.0
                status = "QUALIFIED"
                fit_reason = f"ICP Match: Mid-market organization with {employee_count} employees in '{industry}'. High vulnerability to SaaS & vendor risk."
            elif employee_count > 5000:
                fit_score = 70.0
                status = "QUALIFIED"
                fit_reason = f"Enterprise Fit: Large organization with {employee_count} employees. High volume of sensitive data and GRC complexity."
            else:
                fit_score = 30.0
                status = "DISQUALIFIED"
                fit_reason = f"Outside ICP: Too small ({employee_count} employees) for enterprise sales focus."
        else:
            # Fallback: Default to Gemini's estimated fit score if size is completely unknown
            fit_score = analysis.get("fitScore", 50.0)
            status = "QUALIFIED" if fit_score >= MIN_QUALIFY_FIT_SCORE else "DISQUALIFIED"
            fit_reason = analysis.get("fitReason", "Evaluated via breach scale and entity type (size unknown).")

        # Update analysis payload with resolved firmographics
        analysis["employeeCount"] = employee_count
        analysis["industry"] = industry
        analysis["fitScore"] = fit_score
        analysis["status"] = status
        analysis["fitReason"] = fit_reason

        print(f"    Domain:       {domain or 'None'}")
        print(f"    Employees:    {employee_count if employee_count is not None else 'Unknown'}")
        print(f"    Fit Score:    {fit_score}/100")
        print(f"    Status:       {status}")
        print(f"    Fit Reason:   {fit_reason}")

        # D. Store company in RRR platform database immediately
        db_company_id = None
        try:
            # The API accepts either a single company object or an array of objects
            res = requests.post(API_URL, json=analysis, headers=headers, timeout=15)
            res.raise_for_status()
            ingest_res = res.json()
            companies = ingest_res.get("companies", [])
            if companies:
                db_company_id = companies[0].get("id")
                print(f"    ✓ Ingested: Company ID is {db_company_id}")
        except Exception as e:
            print(f"    ✗ [Error] DB Ingestion failed: {e}")

        # E. Enrich contacts immediately if qualified and saved successfully
        if status == "QUALIFIED" and db_company_id:
            print(f"    Running Explorium Contact Enrichment...")
            contacts = enrich_leads_via_explorium(db_company_id, analysis["name"], domain)
            
            for contact in contacts:
                contact_payload = {
                    "companyId": db_company_id,
                    **contact
                }
                try:
                    c_res = requests.post(CONTACTS_API_URL, json=contact_payload, headers=headers, timeout=10)
                    if c_res.status_code == 201:
                        print(f"      ✓ Saved Contact: {contact['fullName']} ({contact['title']}) → {contact['email']}")
                    else:
                        print(f"      ✗ Failed to save contact {contact['fullName']}: {c_res.text}")
                except Exception as e:
                    print(f"      ✗ Request error saving contact: {e}")

        # F. Remove processed item from list and update file state immediately
        new_entries.pop(0)
        try:
            with open(NEW_ENTRIES_FILE, "w") as f:
                json.dump(new_entries, f, indent=2)
        except Exception as e:
            print(f"    [Warning] Failed to save updated entries JSON: {e}")

        # Be polite to Gemini API rate limits
        time.sleep(1)

    # Cleanup the empty hhs_new_entries.json
    try:
        NEW_ENTRIES_FILE.unlink()
        print(f"\nCleaned up {NEW_ENTRIES_FILE.name}")
    except Exception:
        pass

    print("\n=== Processing & Enrichment Completed ===")

if __name__ == "__main__":
    main()
