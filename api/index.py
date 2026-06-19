from flask import Flask, Response, request
import re
import subprocess
import requests
import json
import base64
import os
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

app = Flask(__name__)

# ==================== CONFIG ====================
WATCH_URL = "https://fibwatch.art/watch/naagin-2025-s07e48-hindi-jh-web-dl-720p_l6SN6MEkd7mT66S.html"
M3U_RAW_URL = "https://raw.githubusercontent.com/mdakash648/MovieM3u/refs/heads/main/playlist.m3u"

GITHUB_REPO   = "mdakash648/MovieM3u"
GITHUB_BRANCH = "main"
GITHUB_FILE   = "playlist.m3u"
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": WATCH_URL,
}
# ================================================


# ─────────────────────────────────────────────
#  Existing helpers (unchanged)
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
#  Auto-update helpers
# ─────────────────────────────────────────────

def _get_bd_date_str() -> str:
    """Return current date in Bangladesh timezone as 'DD MONTH YYYY'."""
    bd_time = datetime.now(timezone(timedelta(hours=6)))
    return bd_time.strftime("%d %B %Y").upper()


def _parse_auto_search_blocks(content: str):
    """
    Parse playlist content and return a list of blocks.
    Each block is a dict:
      {
        "search_url": str,          # value from auto_search_update line
        "series_name": str,         # e.g. "Naagin"
        "season": str,              # e.g. "S07"
        "last_ep": int,             # e.g. 49
        "group_title": str,         # from #EXTINF lines of this block
        "block_start": int,         # line index where auto_search_update is
        "block_end": int,           # last line index belonging to this block
      }
    """
    lines = content.splitlines(keepends=True)
    blocks = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # Detect auto_search_update line
        m = re.match(r'^auto_search_update\s*:\s*"(.+)"', stripped)
        if m:
            search_url = m.group(1)
            # Extract series name, season, episode from the URL keyword
            kw_match = re.search(r'keyword=(.+)$', search_url)
            keyword = requests.utils.unquote(kw_match.group(1)) if kw_match else ""
            # e.g. "Naagin (2025) S07E49"
            ep_match = re.search(r'(S\d+)E(\d+)', keyword, re.IGNORECASE)
            series_match = re.match(r'^([^\(]+)', keyword)
            series_name = series_match.group(1).strip() if series_match else keyword
            season = ep_match.group(1).upper() if ep_match else "S01"
            last_ep = int(ep_match.group(2)) if ep_match else 1

            # Scan forward to find the group-title used for this series block
            group_title = ""
            block_end = i
            j = i + 1
            while j < len(lines):
                ls = lines[j].strip()
                if re.match(r'^auto_search_update\s*:', ls):
                    break  # next block starts
                gt_match = re.search(r'group-title="([^"]+)"', ls)
                if gt_match and not group_title:
                    group_title = gt_match.group(1)
                block_end = j
                j += 1

            blocks.append({
                "search_url": search_url,
                "series_name": series_name,
                "season": season,
                "last_ep": last_ep,
                "group_title": group_title,
                "block_start": i,
                "block_end": block_end,
            })
        i += 1
    return blocks


def _build_next_search_url(search_url: str, next_ep: int) -> str:
    """Replace the episode number in the search URL with next_ep."""
    def replace_ep(m):
        return m.group(1) + str(next_ep)
    new_url = re.sub(r'(S\d+E)(\d+)', replace_ep, search_url, flags=re.IGNORECASE)
    return new_url


def _search_fibwatch(search_url: str, series_name: str, season: str, next_ep: int):
    """
    Search fibwatch for the next episode.
    Returns list of (title, page_url) tuples that match — single-episode only.
    Prefers 1080p, falls back to 720p.
    Returns None if no valid result found.
    """
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    # Check for "no results" indicators
    if any(x in html for x in [
        "সঠিক বানান গুগুল থেকে সার্চ করে",
        "no result",
        "not found",
        "কোনো ফলাফল পাওয়া যায়নি",
    ]):
        return None

    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    ep_str = f"E{next_ep:02d}"  # e.g. E50
    ep_str_alt = f"E{next_ep}"   # e.g. E50

    for a in soup.find_all("a", href=True):
        title = (a.get_text(strip=True) or a.get("title", "")).strip()
        href = a["href"]

        if not title or not re.match(r'https?://', href):
            # Make absolute if relative
            if href.startswith("/"):
                href = "https://fibwatch.art" + href
            else:
                continue

        # Must contain the series name (fuzzy)
        if not re.search(re.escape(series_name[:5]), title, re.IGNORECASE):
            continue

        # Must contain the season
        if not re.search(re.escape(season), title, re.IGNORECASE):
            continue

        # Must contain the exact episode — but NOT a range like E49-50
        ep_pattern = rf'{season}E{next_ep:02d}(?!\s*[-–]\s*\d)'
        if not re.search(ep_pattern, title, re.IGNORECASE):
            continue

        # Skip multi-episode entries (contains a dash between episode numbers)
        if re.search(rf'{season}E\d+[-–]\d+', title, re.IGNORECASE):
            continue

        # Detect resolution
        res_1080 = bool(re.search(r'1080', title))
        res_720  = bool(re.search(r'720', title))

        candidates.append({
            "title": title,
            "href": href,
            "res_1080": res_1080,
            "res_720": res_720,
        })

    if not candidates:
        return None

    # Prefer 1080p; if none, take 720p; if neither tagged, take first
    preferred = [c for c in candidates if c["res_1080"]]
    if not preferred:
        preferred = [c for c in candidates if c["res_720"]]
    if not preferred:
        preferred = candidates

    return preferred[0]


def _extract_media_from_page(page_url: str) -> str | None:
    """Extract direct .mkv/.mp4 URL from a fibwatch watch page."""
    return extract_media_url(page_url)


def _build_m3u_entry(
    group_title: str,
    title_label: str,
    page_url: str,
    media_url: str,
    date_str: str,
) -> str:
    """Build the M3U block string for a new episode entry."""
    comment = (
        f"# ==========================================\n"
        f"# Add : {date_str}\n"
        f"# CONTENT - {title_label}\n"
        f"# ==========================================\n"
    )
    entry = (
        f"#EXTINF:-1 group-title=\"{group_title}\" -1,{title_label}\n"
        f"#EXTVLCOPT:http-referrer={page_url}\n"
        f"#EXTVLCOPT:http-user-agent={HEADERS['User-Agent']}\n"
        f"{media_url}\n"
    )
    return comment + entry


def _make_title_label(page_title: str, media_url: str) -> str:
    """
    Build a clean label like [Fibwatch.Com]Naagin.S07E50.720P
    from the page title or media URL filename.
    """
    # Try to get filename from media URL
    fname = media_url.rstrip("/").split("/")[-1]
    fname = re.sub(r'\.(mkv|mp4)$', '', fname, flags=re.IGNORECASE)
    if fname and len(fname) > 5:
        # Already clean — use as-is but strip extension
        return fname

    # Fallback: derive from page title
    clean = re.sub(r'\s+', '.', page_title.strip())
    clean = re.sub(r'[^\w.\-]', '', clean)
    return f"[Fibwatch.Com]{clean}"


# ─────────────────────────────────────────────
#  GitHub helpers
# ─────────────────────────────────────────────

def _github_get_file():
    """
    Fetch playlist.m3u from GitHub API and return (content_str, sha).
    Strips base64 line-wraps (GitHub wraps at 60 chars) before decoding.
    """
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    gh_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    resp = requests.get(url, headers=gh_headers, params={"ref": GITHUB_BRANCH}, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub GET failed: {resp.status_code} {resp.text}")
    data = resp.json()
    sha = data["sha"]
    # GitHub wraps base64 at 60 chars — strip all whitespace before decoding
    raw_b64 = data["content"].replace("
", "").replace("
", "").strip()
    content = base64.b64decode(raw_b64).decode("utf-8")
    return content, sha


def _github_push_file(new_content: str, sha: str, commit_msg: str):
    """Push updated playlist.m3u to GitHub. Re-fetches sha right before PUT to avoid 409."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    gh_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }
    # Re-fetch latest sha just before PUT to avoid race-condition 409
    check = requests.get(url, headers=gh_headers, params={"ref": GITHUB_BRANCH}, timeout=10)
    if check.status_code == 200:
        sha = check.json()["sha"]
    encoded = base64.b64encode(new_content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_msg,
        "content": encoded,
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    resp = requests.put(url, headers=gh_headers, json=payload, timeout=15)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GitHub PUT failed: {resp.status_code} {resp.text}")
    return resp.json()


# ─────────────────────────────────────────────
#  Core auto-update logic
# ─────────────────────────────────────────────

def run_auto_update() -> dict:
    """
    Main function called by the cron endpoint.
    Reads playlist from GitHub, processes all auto_search_update blocks,
    appends new episodes if found, then pushes back to GitHub.
    Returns a summary dict.
    """
    if not GITHUB_TOKEN:
        return {"status": "error", "message": "GITHUB_TOKEN not set"}

    try:
        content, sha = _github_get_file()
    except Exception as e:
        return {"status": "error", "message": str(e)}

    blocks = _parse_auto_search_blocks(content)
    if not blocks:
        return {"status": "ok", "message": "No auto_search_update blocks found"}

    date_str = _get_bd_date_str()
    added_episodes = []
    new_content = content  # we'll append to this

    for block in blocks:
        next_ep = block["last_ep"] + 1
        next_search_url = _build_next_search_url(block["search_url"], next_ep)

        result = _search_fibwatch(
            next_search_url,
            block["series_name"],
            block["season"],
            next_ep,
        )
        if not result:
            # No new episode found — stop processing this block
            continue

        page_url = result["href"]
        media_url = _extract_media_from_page(page_url)
        if not media_url:
            continue

        title_label = _make_title_label(result["title"], media_url)
        group_title = block["group_title"] or block["series_name"]

        new_entry = _build_m3u_entry(
            group_title=group_title,
            title_label=title_label,
            page_url=page_url,
            media_url=media_url,
            date_str=date_str,
        )

        # Append after the last line of this block
        # We insert after block_end in the current new_content
        # Since we're appending sequentially, just add to end of content
        if not new_content.endswith("\n"):
            new_content += "\n"
        new_content += new_entry

        added_episodes.append({
            "episode": f"{block['season']}E{next_ep:02d}",
            "title": title_label,
            "media_url": media_url,
        })

    if not added_episodes:
        return {"status": "ok", "message": "No new episodes found", "checked": [b["series_name"] for b in blocks]}

    commit_msg = f"Auto-update: added {', '.join(e['episode'] for e in added_episodes)} [{date_str}]"
    try:
        _github_push_file(new_content, sha, commit_msg)
    except Exception as e:
        return {"status": "error", "message": f"GitHub push failed: {str(e)}", "added": added_episodes}

    return {
        "status": "ok",
        "message": f"Added {len(added_episodes)} episode(s)",
        "added": added_episodes,
    }


# ─────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────

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


@app.route('/auto-update', methods=['GET', 'POST'])
def auto_update_endpoint():
    """
    Cron endpoint — called by Vercel at 00:00 BD time (18:00 UTC).
    Also callable manually via GET /auto-update
    """
    result = run_auto_update()
    status_code = 200 if result.get("status") == "ok" else 500
    return cors_headers(
        Response(json.dumps(result, ensure_ascii=False), status=status_code, mimetype='application/json')
    )


@app.route('/audioinfo', methods=['GET', 'OPTIONS'])
def audio_info():
    if request.method == 'OPTIONS':
        return cors_headers(Response('', status=204))

    video_url = request.args.get('url', '')
    if not video_url or not re.match(r'https?://', video_url):
        return cors_headers(Response('{"error":"Invalid URL"}', status=400, mimetype='application/json'))

    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-select_streams', 'a',
                '-headers', f'User-Agent: {HEADERS["User-Agent"]}\r\nReferer: {HEADERS["Referer"]}\r\n',
                video_url
            ],
            capture_output=True, text=True, timeout=20
        )
        data = json.loads(result.stdout or '{"streams":[]}')
        tracks = []
        for i, s in enumerate(data.get('streams', [])):
            tags = s.get('tags', {})
            lang  = tags.get('language') or tags.get('LANGUAGE') or ''
            title = tags.get('title')    or tags.get('TITLE')    or ''
            label = title or lang or f'Track {i + 1}'
            tracks.append({'index': i, 'lang': lang, 'title': title, 'label': label})
        return cors_headers(Response(json.dumps({'tracks': tracks}), mimetype='application/json'))
    except Exception as e:
        return cors_headers(Response(json.dumps({'tracks': [], 'error': str(e)}), mimetype='application/json'))


@app.route('/proxy', methods=['GET', 'OPTIONS'])
def proxy_video():
    if request.method == 'OPTIONS':
        return cors_headers(Response('', status=204))

    video_url = request.args.get('url', '')
    if not video_url:
        return cors_headers(Response('Missing url parameter', status=400))
    if not re.match(r'https?://', video_url):
        return cors_headers(Response('Invalid URL', status=400))

    audio_track = request.args.get('audio', None)

    if audio_track is not None:
        try:
            track_idx = int(audio_track)
        except ValueError:
            track_idx = 0

        def generate_ffmpeg():
            cmd = [
                'ffmpeg', '-v', 'quiet',
                '-headers', f'User-Agent: {HEADERS["User-Agent"]}\r\nReferer: {HEADERS["Referer"]}\r\n',
                '-i', video_url,
                '-map', '0:v:0',
                '-map', f'0:a:{track_idx}',
                '-c', 'copy',
                '-movflags', 'frag_keyframe+empty_moov+faststart',
                '-f', 'mp4',
                'pipe:1'
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.kill()
                proc.wait()

        resp = Response(generate_ffmpeg(), mimetype='video/mp4')
        resp.headers['Accept-Ranges'] = 'none'
        return cors_headers(resp)

    range_header = request.headers.get('Range', None)
    req_headers  = dict(HEADERS)
    if range_header:
        req_headers['Range'] = range_header

    try:
        upstream = requests.get(video_url, headers=req_headers, stream=True, timeout=15)
    except Exception as e:
        return cors_headers(Response(f'Upstream error: {e}', status=502))

    content_type = upstream.headers.get('Content-Type', 'video/mp4')
    if 'mkv' in video_url.lower() or 'matroska' in content_type.lower():
        content_type = 'video/x-matroska'
    elif 'mp4' in video_url.lower():
        content_type = 'video/mp4'

    def generate():
        for chunk in upstream.iter_content(chunk_size=65536):
            if chunk:
                yield chunk

    resp = Response(generate(), status=upstream.status_code, mimetype=content_type, direct_passthrough=True)

    for h in ('Content-Range', 'Content-Length', 'Accept-Ranges', 'Last-Modified', 'ETag'):
        val = upstream.headers.get(h)
        if val:
            resp.headers[h] = val

    if 'Accept-Ranges' not in resp.headers:
        resp.headers['Accept-Ranges'] = 'bytes'

    return cors_headers(resp)


if __name__ == '__main__':
    app.run(debug=True)