from flask import Flask, Response
import re
import requests
from bs4 import BeautifulSoup

app = Flask(__name__) # Vercel এখন এই টপ-লেভেল 'app' ভ্যারিয়েবলটা খুঁজে পাবে 🦾

# ==================== CONFIG ====================
WATCH_URL = "https://fibwatch.art/watch/naagin-2025-s07e48-hindi-jh-web-dl-720p_l6SN6MEkd7mT66S.html"
# ⚠️ নিচে তোমার গিটহাবের playlist.m3u ফাইলের RAW URL টা বসাও
M3U_RAW_URL = "https://raw.githubusercontent.com/mdakash648/MovieM3u/refs/heads/main/playlist.m3u"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": WATCH_URL,
}
# ================================================

def extract_media_url(watch_url: str) -> str | None:
    try:
        resp = requests.get(watch_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception:
        return None

    html = resp.text
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
            return url.strip().strip('"\'')

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src") or tag.get("data-src") or tag.get("data-url")
        if src and re.search(r'\.(mkv|mp4)(\?|$)', src, re.IGNORECASE):
            return src

    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r'["\']?(https?://[^\s\'"]+\.(?:mkv|mp4))["\']?', text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None

def extract_domain(url: str) -> str:
    match = re.match(r'(https?://[^/]+)', url)
    return match.group(1) if match else ""

def generate_updated_m3u(new_media_url: str) -> str:
    try:
        resp = requests.get(M3U_RAW_URL, timeout=10)
        if resp.status_code != 200:
            return f"# ERROR: Could not fetch M3U from GitHub. Status: {resp.status_code}"
        content = resp.text
    except Exception as e:
        return f"# ERROR: {str(e)}"

    lines = content.splitlines(keepends=True)
    new_domain = extract_domain(new_media_url)
    if not new_domain:
        return content

    new_lines = []
    is_fibwatch_entry = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#EXTINF"):
            is_fibwatch_entry = "[Fibwatch.Com]" in stripped
            new_lines.append(line)

        elif re.match(r'https?://', stripped) and re.search(r'\.(mkv|mp4)', stripped, re.IGNORECASE):
            if is_fibwatch_entry:
                old_domain = extract_domain(stripped)
                if old_domain and old_domain != new_domain:
                    new_line = stripped.replace(old_domain, new_domain, 1) + "\n"
                    new_lines.append(new_line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
            is_fibwatch_entry = False
        else:
            new_lines.append(line)

    return "".join(new_lines)


@app.route('/playlist.m3u', methods=['GET'])
@app.route('/', methods=['GET'])
def get_playlist():
    media_url = extract_media_url(WATCH_URL)
    
    if media_url:
        m3u_content = generate_updated_m3u(media_url)
    else:
        try:
            m3u_content = requests.get(M3U_RAW_URL, timeout=10).text
        except Exception:
            m3u_content = "#EXTM3U\n# ERROR: Could not extract media URL"

    # Flask Response তৈরি করা (M3U ফরম্যাট এবং CORS হেডারসহ)
    response = Response(m3u_content, mimetype='application/x-mpegurl')
    response.headers['Content-Disposition'] = 'inline; filename="playlist.m3u"'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

# লোকাল পিসিতে টেস্ট করার জন্য (অপশনাল)
if __name__ == '__main__':
    app.run(debug=True)