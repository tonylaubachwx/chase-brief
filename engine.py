import asyncio
import datetime
import re
from typing import Dict, List, Optional
import httpx

HEADERS = {
    "User-Agent": "(ChaseBriefGenerator/1.1, contact: stormbriefs@weatherops.org)",
    "Accept": "application/geo+json, text/html, text/plain, */*"
}

# --- 1. TIMESTAMP & STRING CLEANERS ---

def compute_age(issuance_time_str: Optional[str]) -> str:
    """Calculates human-readable age of an issuance timestamp."""
    if not issuance_time_str:
        return "Unknown"
    try:
        issuance_dt = datetime.datetime.fromisoformat(issuance_time_str.replace("Z", "+00:00"))
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        diff = now_dt - issuance_dt
        hours, remainder = divmod(int(diff.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m ago"
        return f"{minutes}m ago"
    except Exception:
        return "Recent"

def clean_truncate(text: str, max_chars: int = 4000) -> str:
    """Safely trims text to max_chars without cutting off in the middle of a sentence."""
    if len(text) <= max_chars:
        return text.strip()
    
    truncated = text[:max_chars]
    # Find the last period followed by whitespace or newline
    last_period = max(truncated.rfind(". "), truncated.rfind(".\n"))
    if last_period != -1:
        return truncated[:last_period + 1].strip()
    return truncated.strip()

# --- 2. REGEX SECTION SANITIZERS ---

def sanitize_afd(raw_text: str, mode: str) -> str:
    """Strips non-convective sections and isolates mode-relevant paragraphs."""
    # 1. Strip non-convective sections
    cleaned = re.split(
        r"\.(?:AVIATION|FIRE WEATHER|MARINE|CLIMATE|HYDROLOGY)\b", 
        raw_text, 
        flags=re.IGNORECASE
    )[0]
    
    # 2. Extract targeted sections based on forecast horizon
    if "Day-Of" in mode:
        match = re.search(
            r"(\.(?:NEAR TERM|MESOSCALE|UPDATE|DISCUSSION)[\s\S]*?)(?=\.(?:SHORT TERM|LONG TERM|\$\$)|$)", 
            cleaned, 
            re.IGNORECASE
        )
        if match:
            return clean_truncate(match.group(1))
    elif "Day-Before" in mode:
        match = re.search(
            r"(\.SHORT TERM[\s\S]*?)(?=\.(?:LONG TERM|\$\$)|$)", 
            cleaned, 
            re.IGNORECASE
        )
        if match:
            return clean_truncate(match.group(1))
    elif "Extended" in mode or "Home-Base" in mode:
        match = re.search(
            r"(\.(?:LONG TERM|EXTENDED)[\s\S]*?)(?=\.(?:\$\$)|$)", 
            cleaned, 
            re.IGNORECASE
        )
        if match:
            return clean_truncate(match.group(1))
            
    return clean_truncate(cleaned)

def sanitize_hwo(raw_text: str, mode: str) -> str:
    """Strips HWO text for Day 1 vs Days 2-7."""
    if "Day-Of" in mode:
        match = re.search(
            r"(\.DAY ONE[\s\S]*?)(?=\.DAYS TWO THROUGH SEVEN|\$\$|$)", 
            raw_text, 
            re.IGNORECASE
        )
        return match.group(1).strip() if match else clean_truncate(raw_text, 1500)
    elif "Day-Before" in mode:
        match = re.search(
            r"(\.DAYS TWO THROUGH SEVEN[\s\S]*?)(?=\.SPOTTER INFORMATION STATEMENT|\$\$|$)", 
            raw_text, 
            re.IGNORECASE
        )
        return match.group(1).strip() if match else clean_truncate(raw_text, 1500)
    return ""

# --- 3. NWS & SPC API CLIENTS ---

async def fetch_nws_product(client: httpx.AsyncClient, wfo: str, ptype: str) -> Dict[str, str]:
    """Fetches latest text bulletin for a given WFO."""
    url = f"https://api.weather.gov/products/types/{ptype}/locations/{wfo}"
    try:
        resp = await client.get(url, headers=HEADERS, timeout=7.0)
        if resp.status_code != 200:
            return {"wfo": wfo, "type": ptype, "text": "", "issuance": None}
        graph = resp.json().get("@graph", [])
        if not graph:
            return {"wfo": wfo, "type": ptype, "text": "", "issuance": None}
            
        latest_id = graph[0]["@id"]
        issuance_time = graph[0].get("issuanceTime")
        prod_resp = await client.get(latest_id, headers=HEADERS, timeout=7.0)
        if prod_resp.status_code == 200:
            return {
                "wfo": wfo,
                "type": ptype,
                "text": prod_resp.json().get("productText", ""),
                "issuance": issuance_time
            }
    except Exception:
        pass
    return {"wfo": wfo, "type": ptype, "text": "", "issuance": None}

async def fetch_spc_convective_text(client: httpx.AsyncClient, mode: str) -> str:
    """
    Fetches latest SPC outlook text via api.weather.gov.
    Falls back directly to spc.noaa.gov HTML/text if API returns empty.
    """
    # 1. Determine Product Target
    if "Day-Of" in mode:
        api_type = "SWODY1"
        spc_html_slug = "day1otlk.html"
    elif "Day-Before" in mode:
        api_type = "SWODY2"
        spc_html_slug = "day2otlk.html"
    else:
        api_type = "SWODY3"
        spc_html_slug = "day3otlk.html"

    # Attempt Primary: api.weather.gov
    try:
        api_url = f"https://api.weather.gov/products/types/{api_type}"
        resp = await client.get(api_url, headers=HEADERS, timeout=7.0)
        if resp.status_code == 200:
            graph = resp.json().get("@graph", [])
            if graph:
                latest_url = graph[0]["@id"]
                prod_resp = await client.get(latest_url, headers=HEADERS, timeout=7.0)
                if prod_resp.status_code == 200:
                    text = prod_resp.json().get("productText", "")
                    if text and len(text.strip()) > 100:
                        return text.strip()
    except Exception:
        pass

    # Attempt Fallback: Direct SPC Server Scrape
    try:
        direct_url = f"https://www.spc.noaa.gov/products/outlook/{spc_html_slug}"
        resp = await client.get(direct_url, headers=HEADERS, timeout=7.0)
        if resp.status_code == 200:
            # Extract content between <pre> tags where SPC renders the discussion
            pre_match = re.search(r"<pre>([\s\S]*?)</pre>", resp.text, re.IGNORECASE)
            if pre_match:
                # Strip internal HTML tags if any exist
                clean_text = re.sub(r"<[^>]+>", "", pre_match.group(1))
                return clean_text.strip()
    except Exception:
        pass

    return "SPC convective discussion text currently unavailable from NWS and SPC servers."

async def fetch_all_discussions(cwa_list: List[str], mode: str) -> Dict[str, dict]:
    """Asynchronously fetches all requested NWS and SPC discussions."""
    results = {}
    async with httpx.AsyncClient() as client:
        tasks = []
        for cwa in cwa_list:
            tasks.append(fetch_nws_product(client, cwa, "AFD"))
            if "Extended" not in mode:
                tasks.append(fetch_nws_product(client, cwa, "HWO"))
        
        spc_task = fetch_spc_convective_text(client, mode)
        responses = await asyncio.gather(*tasks, spc_task, return_exceptions=True)
        
        # Last item is SPC text
        spc_text = responses[-1] if isinstance(responses[-1], str) else "SPC text unavailable."
        
        for item in responses[:-1]:
            if isinstance(item, dict) and item.get("text"):
                wfo = item["wfo"]
                ptype = item["type"]
                if wfo not in results:
                    results[wfo] = {"AFD": "", "HWO": "", "issuance": None, "age": ""}
                
                if ptype == "AFD":
                    results[wfo]["AFD"] = sanitize_afd(item["text"], mode)
                    results[wfo]["issuance"] = item["issuance"]
                    results[wfo]["age"] = compute_age(item["issuance"])
                elif ptype == "HWO":
                    results[wfo]["HWO"] = sanitize_hwo(item["text"], mode)
                    
    return {"spc": spc_text, "offices": results}

# --- 4. PROMPT COMPILER ---

def build_forecaster_prompt(
    data: dict,
    mode: str,
    origin: str,
    objective: str,
    max_drive_hours: int
) -> str:
    """Assembles the final prompt payload."""
    if "Home-Base" in mode:
        return f"""SYSTEM / ROLE:
You are an expert regional operational meteorologist providing a comprehensive synoptic & local weather briefing for North-Central Colorado / Front Range.

LOCATION FOCUS:
- Base: {origin}
- Anchor Offices: {', '.join(data.get('offices', {}).keys())}

OUTPUT REQUIREMENTS:
1. Front Range Baseline: Temperatures, wind regimes (Chinook vs. Denver Cyclone/upslope), frontal passages.
2. Hazards Matrix: Thunder/convective risks, winter precip/snow levels (foothills vs plains), fire weather.
3. Synoptic Drivers: Upstream moisture, Pacific waves, or Continental Divide interactions over the next 3-7 days.

---
=== REGIONAL NWS OFFICE DISCUSSIONS ===
""" + "\n".join([f"--- [{wfo} | Updated: {c['age']}] ---\n{c['AFD']}" for wfo, c in data.get("offices", {}).items()])

    return f"""SYSTEM / ROLE:
You are an expert mesoscale meteorologist, broadcast chase strategist, and field forecaster.
Analyze the provided official SPC convective text and localized NWS AFDs/HWOs to deliver up to 3 prioritized chase targets.

MISSION LOGISTICS & CRITERIA:
- Analysis Horizon: {mode}
- Origin Point: {origin}
- Max One-Way Travel Window: ≤ {max_drive_hours} hours
- Operational Objective: {objective}

CRITICAL FORECAST & CORRIDOR DIRECTIVES:
1. Target Areas Must Be Corridors: Define targets as highway/boundary corridors (e.g., "City A to City B along US-XXX"), not single isolated towns.
2. Storm Motion & Speed Contrast: In every target corridor, explicitly state:
   - Expected initiation window (UTC and Local).
   - Storm motion direction and forward speed in mph (to allow direct comparison between fast-moving vs. slower, more manageable setups across targets).
3. Conditionality & Secondary Modes:
   - Detail the primary atmospheric hurdles (e.g., strong capping, veered surface winds, boundary-parallel shear).
   - If the primary hazard (e.g., tornado threat) is conditional or fails, identify secondary production modes (gorilla hail, LP structure, photogenic shelf clouds, high-wind derecho corridors) that justify the excursion.
4. Terrain & Chaseability Check: Factor in local geography, road grid continuity, river crossings, and tree cover hazards (e.g., Sandhills gaps, Ozark tree density, unpaved/clay road concerns).
5. Assignment Pitch: End with a single, standalone 3-4 sentence paragraph tailored for news desks/production management summarizing: Target area, timing, severe hazard magnitude, and visual footage potential.

---
=== SPC CONVECTIVE OUTLOOK & SYNOPSIS ===
{data.get('spc', 'No SPC text provided.')}

=== LOCAL NWS OFFICE DISCUSSIONS & HAZARDS ===
""" + "\n".join([f"--- [{wfo} | AFD Updated: {c['age']}] ---\n[AFD EXCERPT]\n{c['AFD']}\n[HWO EXCERPT]\n{c['HWO']}" for wfo, c in data.get("offices", {}).items()])