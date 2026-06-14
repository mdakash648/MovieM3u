import re
import requests
import json
import time
import os
from typing import Dict, Optional

try:
    import google.generativeai as genai
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

# ==================== CONFIGURATION ====================
# Input and Output files
INPUT_M3U = "Live tv.m3u"
OUTPUT_M3U = "updated_Live_tv.m3u"

# IPTV-org API endpoints (Primary sources for logos)
CHANNELS_API = "https://iptv-org.github.io/api/channels.json"
LOGOS_API = "https://iptv-org.github.io/api/logos.json"

# Delay between searches (if using fallback)
SEARCH_DELAY = 2.0 

# Optional: Gemini API Key for search grounding fallback
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if HAS_GENAI and GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Initialize Gemini with Search Grounding tool
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        tools=[{"google_search": {}}]
    )
else:
    model = None
# ========================================================

def fetch_iptv_org_data() -> Dict[str, str]:
    """Fetch channel data from iptv-org and create a lookup dictionary."""
    print("[*] Fetching logo database from iptv-org...")
    logo_map = {}
    try:
        # Fetch channels (contains name and logo)
        resp = requests.get(CHANNELS_API, timeout=30)
        resp.raise_for_status()
        channels = resp.json()
        
        for ch in channels:
            name = ch.get("name")
            logo = ch.get("logo")
            if name and logo:
                # Store by name (lowercase for case-insensitive matching)
                logo_map[name.lower()] = logo
                
        # Also fetch logos API (contains alternative logos)
        resp = requests.get(LOGOS_API, timeout=30)
        resp.raise_for_status()
        logos = resp.json()
        for l in logos:
            channel_id = l.get("channel")
            url = l.get("url")
            if channel_id and url:
                # Map by channel ID if not already present
                id_clean = channel_id.split(".")[0].lower() # e.g. "BTV.bd" -> "btv"
                if id_clean not in logo_map:
                    logo_map[id_clean] = url

        print(f"[+] Loaded {len(logo_map)} logos from database.")
    except Exception as e:
        print(f"[!] Warning: Failed to fetch iptv-org data: {e}")
    
    return logo_map

def find_logo_gemini(channel_name: str) -> Optional[str]:
    """Fallback search using Gemini API with Search Grounding."""
    if not model:
        return None
    
    print(f"[*] Gemini searching for logo: {channel_name}...")
    try:
        prompt = (
            f"Find the official, high-quality, public logo image URL for the TV channel: {channel_name}. "
            "Respond ONLY with the direct image URL (png, jpg, or svg). "
            "Prioritize open-source or official broadcasting URLs."
        )
        response = model.generate_content(prompt)
        
        # Extract URL from response text
        match = re.search(r'(https?://[^\s\'"]+\.(?:png|jpg|jpeg|svg|webp))', response.text)
        if match:
            url = match.group(1)
            print(f"[+] Gemini found: {url}")
            return url
    except Exception as e:
        print(f"[!] Gemini search failed for {channel_name}: {e}")
    
    return None

def clean_channel_name(name: str) -> str:
    """Clean channel name from suffixes and tags."""
    # Remove things like (m3u4u), [Fibwatch.Com], HD, SD, etc.
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\b(HD|SD|FHD|4K|720p|1080p)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'-', ' ', name)
    return name.strip()

def update_tvg_logo(extinf_line: str, new_logo_url: str) -> str:
    """Update or insert the tvg-logo attribute in an #EXTINF line."""
    if 'tvg-logo="' in extinf_line:
        # Replace existing logo
        return re.sub(r'tvg-logo="[^"]*"', f'tvg-logo="{new_logo_url}"', extinf_line)
    else:
        # Insert logo before the comma (channel name separator)
        if "," in extinf_line:
            parts = extinf_line.rsplit(",", 1)
            return f'{parts[0]} tvg-logo="{new_logo_url}",{parts[1]}'
        return extinf_line

def main():
    if not os.path.exists(INPUT_M3U):
        print(f"[!] Error: {INPUT_M3U} not found.")
        return

    # 1. Load logo database
    logo_lookup = fetch_iptv_org_data()

    # 2. Read M3U file
    with open(INPUT_M3U, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated_lines = []
    updated_count = 0
    skipped_count = 0

    print(f"[*] Processing {len(lines)} lines from {INPUT_M3U}...")

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#EXTINF"):
            # Extract channel name (everything after the last comma)
            channel_name = stripped.split(",")[-1].strip()
            # Clean name (remove suffixes like "HD", "[2]", etc if needed)
            clean_name = clean_channel_name(channel_name)
            
            logo_url = None
            
            # Try 1: Exact match in iptv-org
            logo_url = logo_lookup.get(channel_name.lower())
            
            # Try 2: Clean name match in iptv-org
            if not logo_url:
                logo_url = logo_lookup.get(clean_name.lower())
                
            # Try 3: Fallback search (if enabled)
            if not logo_url and GEMINI_API_KEY:
                logo_url = find_logo_gemini(clean_name)
                time.sleep(SEARCH_DELAY)

            if logo_url:
                new_line = update_tvg_logo(line, logo_url)
                updated_lines.append(new_line)
                updated_count += 1
            else:
                updated_lines.append(line)
                skipped_count += 1
        else:
            updated_lines.append(line)

    # 3. Save output
    with open(OUTPUT_M3U, "w", encoding="utf-8") as f:
        f.writelines(updated_lines)

    print(f"\n[+] Processing Complete!")
    print(f"    - Updated: {updated_count} logos")
    print(f"    - Unchanged: {skipped_count} logos")
    print(f"    - Output saved to: {OUTPUT_M3U}")

if __name__ == "__main__":
    main()
