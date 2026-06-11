import re
import requests
from bs4 import BeautifulSoup

# ==================== CONFIG ====================
WATCH_URL = "https://fibwatch.art/watch/naagin-2025-s07e48-hindi-jh-web-dl-720p_l6SN6MEkd7mT66S.html"
M3U_FILE = "player.m3u"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": WATCH_URL,
}
# ================================================

def extract_media_url(watch_url: str) -> str | None:
    """fibwatch page থেকে .mkv বা .mp4 URL extract করো"""
    print(f"[*] Fetching page: {watch_url}")
    try:
        resp = requests.get(watch_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"[!] Failed to fetch page: {e}")
        return None

    html = resp.text

    # Method 1: Direct regex search in HTML source
    patterns = [
        r'https?://[^\s\'"<>]+\.mkv',
        r'https?://[^\s\'"<>]+\.mp4',
        r'file\s*:\s*["\']?(https?://[^\s\'"]+\.(?:mkv|mp4))',
        r'src\s*[=:]\s*["\']?(https?://[^\s\'"]+\.(?:mkv|mp4))',
    ]

    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            url = match.group(1) if match.lastindex else match.group(0)
            url = url.strip().strip('"\'')
            print(f"[+] Found media URL (regex): {url}")
            return url

    # Method 2: BeautifulSoup - video/source tags
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-url")
        if src and re.search(r'\.(mkv|mp4)(\?|$)', src, re.IGNORECASE):
            print(f"[+] Found media URL (tag): {src}")
            return src

    # Method 3: JSON / JS variable inside <script>
    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(
            r'["\']?(https?://[^\s\'"]+\.(?:mkv|mp4))["\']?',
            text, re.IGNORECASE
        )
        if match:
            url = match.group(1)
            print(f"[+] Found media URL (script tag): {url}")
            return url

    print("[!] No media URL found.")
    return None


def extract_domain(url: str) -> str:
    """URL থেকে domain (scheme + host) বের করো"""
    match = re.match(r'(https?://[^/]+)', url)
    return match.group(1) if match else ""


def update_m3u(m3u_path: str, new_media_url: str) -> bool:
    """
    player.m3u তে শেষ media URL লাইনটা নতুন URL দিয়ে replace করো।
    শুধু domain বদলায়, path একই থাকে।
    """
    try:
        with open(m3u_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"[!] M3U file not found: {m3u_path}")
        return False

    # শেষ http URL লাইন find করো (media link)
    lines = content.splitlines(keepends=True)
    
    new_domain = extract_domain(new_media_url)
    if not new_domain:
        print("[!] Could not extract domain from new URL.")
        return False

    updated = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        # Media URL line: http দিয়ে শুরু, .mkv বা .mp4 আছে
        if re.match(r'https?://', stripped) and re.search(r'\.(mkv|mp4)', stripped, re.IGNORECASE):
            old_domain = extract_domain(stripped)
            if old_domain and old_domain != new_domain:
                new_line = stripped.replace(old_domain, new_domain, 1) + "\n"
                print(f"[+] Replacing domain:")
                print(f"    OLD: {stripped}")
                print(f"    NEW: {new_line.strip()}")
                new_lines.append(new_line)
                updated = True
            elif old_domain == new_domain:
                print(f"[=] Domain already up-to-date: {new_domain}")
                new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if updated:
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        print(f"[+] M3U file updated: {m3u_path}")
    else:
        print("[=] No changes needed.")

    return updated


if __name__ == "__main__":
    media_url = extract_media_url(WATCH_URL)
    if media_url:
        update_m3u(M3U_FILE, media_url)
    else:
        print("[!] Could not find media URL. M3U not updated.")
        exit(1)