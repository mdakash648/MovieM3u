from flask import Flask, Response, request
import re
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

# ==================== CONFIG ====================
WATCH_URL = "https://fibwatch.art/watch/naagin-2025-s07e48-hindi-jh-web-dl-720p_l6SN6MEkd7mT66S.html"
M3U_RAW_URL = "https://raw.githubusercontent.com/mdakash648/MovieM3u/refs/heads/main/playlist.m3u"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": WATCH_URL,
}
# ================================================

def extract_media_url(watch_url: str) -> str | None:
    try:
        resp = requests.get(watch_url, headers=HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

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

    try:
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
    except Exception:
        pass

    return None

def extract_domain(url: str) -> str:
    match = re.match(r'(https?://[^/]+)', url)
    return match.group(1) if match else ""

def generate_updated_m3u(new_media_url: str) -> str:
    try:
        resp = requests.get(M3U_RAW_URL, timeout=8)
        if resp.status_code != 200:
            return f"#EXTM3U\n# ERROR: Could not fetch M3U from GitHub. Status: {resp.status_code}"
        content = resp.text
    except Exception as e:
        return f"#EXTM3U\n# ERROR: {str(e)}"

    lines = content.splitlines(keepends=True)
    new_domain = extract_domain(new_media_url)
    if not new_domain:
        return content

    new_lines = []
    is_fibwatch_entry = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#EXTINF"):
            is_fibwatch_entry = "fibwatch.com" in stripped.lower()
            new_lines.append(line)

        elif stripped.startswith("#EXTVLCOPT") or not stripped:
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


def cors_headers(response: Response) -> Response:
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Range, Content-Type'
    response.headers['Access-Control-Expose-Headers'] = 'Content-Range, Content-Length, Accept-Ranges'
    return response


@app.route('/playlist.m3u', methods=['GET'])
@app.route('/', methods=['GET'])
def get_playlist():
    try:
        media_url = extract_media_url(WATCH_URL)
        if media_url:
            m3u_content = generate_updated_m3u(media_url)
        else:
            m3u_content = requests.get(M3U_RAW_URL, timeout=8).text
    except Exception as e:
        m3u_content = f"#EXTM3U\n# GLOBAL ERROR: {str(e)}"

    response = Response(m3u_content, mimetype='application/x-mpegurl')
    response.headers['Content-Disposition'] = 'inline; filename="playlist.m3u"'
    return cors_headers(response)


@app.route('/proxy', methods=['GET', 'OPTIONS'])
def proxy_video():
    """
    Proxy endpoint to stream MKV/MP4 files with proper CORS and Range support.
    Usage: /proxy?url=<encoded_video_url>
    """
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        return cors_headers(resp)

    video_url = request.args.get('url', '')
    if not video_url:
        return cors_headers(Response('Missing url parameter', status=400))

    # Only allow mkv/mp4 files from known domains for security
    if not re.match(r'https?://', video_url):
        return cors_headers(Response('Invalid URL', status=400))

    # Forward Range header for seek support
    range_header = request.headers.get('Range', None)
    req_headers = dict(HEADERS)
    if range_header:
        req_headers['Range'] = range_header

    try:
        upstream = requests.get(
            video_url,
            headers=req_headers,
            stream=True,
            timeout=15
        )
    except Exception as e:
        return cors_headers(Response(f'Upstream error: {e}', status=502))

    # Detect content type
    content_type = upstream.headers.get('Content-Type', 'video/mp4')
    if 'mkv' in video_url.lower() or 'matroska' in content_type.lower():
        content_type = 'video/x-matroska'
    elif 'mp4' in video_url.lower():
        content_type = 'video/mp4'

    status_code = upstream.status_code  # 200 or 206 for partial content

    def generate():
        for chunk in upstream.iter_content(chunk_size=65536):
            if chunk:
                yield chunk

    resp = Response(
        generate(),
        status=status_code,
        mimetype=content_type,
        direct_passthrough=True
    )

    # Forward important headers
    for h in ('Content-Range', 'Content-Length', 'Accept-Ranges', 'Last-Modified', 'ETag'):
        val = upstream.headers.get(h)
        if val:
            resp.headers[h] = val

    if 'Accept-Ranges' not in resp.headers:
        resp.headers['Accept-Ranges'] = 'bytes'

    return cors_headers(resp)


if __name__ == '__main__':
    app.run(debug=True)