from flask import Flask, Response, request
import re
import subprocess
import requests
import json
import base64
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
import concurrent.futures
import urllib.parse

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

# Generic, domain-level headers used for proxy/reachability checks on ANY
# movie's CDN link (bunny's hotlink protection checks the Referer's domain,
# not the exact page — this matches the #EXTVLCOPT headers your M3U player
# already sends). A per-request "referer"/"ua" query param can still override
# this if a stricter, page-exact referer is ever needed.
DEFAULT_REFERER    = "https://fibwatch.art/"
DEFAULT_USER_AGENT = HEADERS["User-Agent"]
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
#  Live CDN domain resolver
#  (fixes dead/rotated b-cdn.net links at play-time)
# ─────────────────────────────────────────────

_CDN_CACHE_TTL = 300  # seconds — how long we trust a discovered "active" domain
_cdn_cache = {"domain": None, "ts": 0}


def _is_url_reachable(url: str, req_headers: dict | None = None) -> bool:
    """Quick HEAD check. Treat 405 (HEAD not allowed) as reachable too."""
    try:
        resp = requests.head(url, headers=req_headers or HEADERS, timeout=6, allow_redirects=True)
        return resp.status_code < 400 or resp.status_code == 405
    except Exception:
        return False


def get_active_cdn_domain(force: bool = False) -> str | None:
    """
    Return the currently-active CDN domain (e.g. 'https://hrtujkk.b-cdn.net'),
    discovered by re-scraping WATCH_URL (which always reflects the latest
    working domain, same as the existing playlist auto-updater does).
    Cached for _CDN_CACHE_TTL seconds so we don't hit fibwatch on every request.
    """
    now = time.time()
    if not force and _cdn_cache["domain"] and (now - _cdn_cache["ts"] < _CDN_CACHE_TTL):
        return _cdn_cache["domain"]

    media_url = extract_media_url(WATCH_URL)
    if media_url:
        domain = extract_domain(media_url)
        if domain:
            _cdn_cache["domain"] = domain
            _cdn_cache["ts"] = now
            return domain

    # scrape failed — fall back to whatever we last knew (may be None)
    return _cdn_cache["domain"]


def resolve_working_url(video_url: str, req_headers: dict | None = None) -> str:
    """
    Given a video URL the user is trying to play, make sure it actually works.
    If the CDN domain in it is dead (rotated), swap in the currently active
    domain (same path/filename kept) and return the fixed URL.
    If the original URL already works, or nothing better can be found,
    return it unchanged.

    Side-effect: if a dead domain is detected, this also kicks off a
    background job that rewrites EVERY Fibwatch entry's CDN domain inside
    playlist.m3u on GitHub — so the whole playlist self-heals, not just the
    one URL the user happened to click.
    """
    if not video_url:
        return video_url

    if _is_url_reachable(video_url, req_headers):
        return video_url

    old_domain = extract_domain(video_url)
    if not old_domain:
        return video_url

    new_domain = get_active_cdn_domain()
    if new_domain and new_domain != old_domain:
        fixed_url = video_url.replace(old_domain, new_domain, 1)
        _trigger_playlist_cdn_sync(new_domain)
        return fixed_url

    return video_url


# ─────────────────────────────────────────────
#  Playlist-wide CDN domain sync (auto-push to GitHub)
#  Triggered whenever resolve_working_url() discovers a rotated domain.
# ─────────────────────────────────────────────

_sync_lock = threading.Lock()
_last_synced_domain = {"domain": None, "ts": 0}
_SYNC_COOLDOWN = _CDN_CACHE_TTL  # don't re-push for the same domain within this window


def _swap_fibwatch_domains(content: str, new_domain: str):
    """
    Rewrite the CDN domain for every playlist entry that belongs to Fibwatch
    (detected via the '[Fibwatch.Com]' title tag on #EXTINF, or a
    fibwatch.art referrer on the #EXTVLCOPT line right above the media URL).
    Non-Fibwatch entries (e.g. circleftp links) are left untouched.
    Returns (new_content, changed: bool).
    """
    lines = content.splitlines(keepends=True)
    new_lines = []
    is_fibwatch_entry = False
    changed = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#EXTINF"):
            is_fibwatch_entry = "fibwatch" in stripped.lower()
            new_lines.append(line)
        elif stripped.startswith("#EXTVLCOPT"):
            if "referrer" in stripped.lower() and "fibwatch" in stripped.lower():
                is_fibwatch_entry = True
            new_lines.append(line)
        elif not stripped:
            new_lines.append(line)
        elif re.match(r'https?://', stripped) and re.search(r'\.(mkv|mp4)', stripped, re.IGNORECASE):
            if is_fibwatch_entry:
                old_domain = extract_domain(stripped)
                if old_domain and old_domain != new_domain:
                    new_lines.append(stripped.replace(old_domain, new_domain, 1) + "\n")
                    changed = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
            is_fibwatch_entry = False
        else:
            new_lines.append(line)

    return "".join(new_lines), changed


def sync_playlist_cdn_domain(new_domain: str) -> dict:
    """
    Pull playlist.m3u from GitHub, swap every Fibwatch entry's CDN domain to
    new_domain, and push back if anything actually changed.
    """
    if not GITHUB_TOKEN:
        print("[cdn-sync] GITHUB_TOKEN is empty/missing — check Vercel env vars")
        return {"status": "error", "message": "GITHUB_TOKEN not set"}

    try:
        content, sha = _github_get_file()
        print(f"[cdn-sync] fetched playlist.m3u, sha={sha[:7]}")
    except Exception as e:
        print(f"[cdn-sync] GitHub GET failed: {e}")
        return {"status": "error", "message": f"GitHub GET failed: {e}"}

    new_content, changed = _swap_fibwatch_domains(content, new_domain)
    if not changed:
        print(f"[cdn-sync] no Fibwatch entries needed updating for domain={new_domain}")
        return {"status": "ok", "message": "No Fibwatch entries needed updating", "domain": new_domain}

    commit_msg = f"Auto CDN domain sync -> {new_domain} [{_get_bd_date_str()}]"
    try:
        _github_push_file(new_content, sha, commit_msg)
        print(f"[cdn-sync] pushed commit: {commit_msg}")
    except Exception as e:
        print(f"[cdn-sync] GitHub push failed: {e}")
        return {"status": "error", "message": f"GitHub push failed: {e}"}

    return {"status": "ok", "message": "Playlist synced", "domain": new_domain}


def _trigger_playlist_cdn_sync(new_domain: str) -> None:
    """
    Push the CDN-domain sync to GitHub *synchronously*, before the HTTP
    response goes out.

    NOTE: this used to fire a background daemon thread so the user's video
    request wasn't delayed by the GitHub round-trip. That works fine on a
    normal long-running server, but breaks silently on Vercel (and most
    other serverless platforms): as soon as the function returns its HTTP
    response, the runtime freezes/kills the instance, so the background
    thread's GitHub API calls never get to finish. The domain swap in the
    response looked correct, but GitHub was never actually updated.

    Making this synchronous costs an extra ~0.5-1.5s only on requests where
    the domain actually changed (rare, and cooled down for _SYNC_COOLDOWN
    seconds), in exchange for the push actually completing.
    """
    now = time.time()
    with _sync_lock:
        if (_last_synced_domain["domain"] == new_domain
                and (now - _last_synced_domain["ts"] < _SYNC_COOLDOWN)):
            print(f"[cdn-sync] skip: '{new_domain}' already synced "
                  f"{now - _last_synced_domain['ts']:.0f}s ago (cooldown={_SYNC_COOLDOWN}s)")
            return
        _last_synced_domain["domain"] = new_domain
        _last_synced_domain["ts"] = now

    print(f"[cdn-sync] starting sync -> {new_domain}")
    try:
        result = sync_playlist_cdn_domain(new_domain)
        print(f"[cdn-sync] result: {result}")
    except Exception as e:
        print(f"[cdn-sync] FAILED: {e}")
        # Let a real failure be retried on the next request instead of
        # being remembered as "already synced".
        with _sync_lock:
            if _last_synced_domain["domain"] == new_domain:
                _last_synced_domain["domain"] = None
                _last_synced_domain["ts"] = 0


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

            # ── Detect mode: "direct" vs "search" ──
            # Direct mode: URL has no search query param (no ?keyword=, ?q=, ?search= etc.)
            # and ends with a numeric ID  e.g. http://new.circleftp.net/content/100576
            is_direct = (
                not re.search(r'[?&](keyword|q|search|query)=', search_url, re.IGNORECASE)
                and re.search(r'/\d+\s*$', search_url.rstrip('/'))
            )

            if is_direct:
                # Direct mode — derive last_id from URL tail number
                id_match = re.search(r'/(\d+)\s*$', search_url.rstrip('/'))
                last_id  = int(id_match.group(1)) if id_match else 0
                # Series name / season / ep come from the existing playlist entries below
                series_name = ""
                season      = ""
                last_ep     = 0
            else:
                # Search mode — extract keyword info as before
                kw_match = re.search(r'keyword=(.+)$', search_url)
                keyword  = requests.utils.unquote(kw_match.group(1)) if kw_match else ""
                ep_match    = re.search(r'(S\d+)E(\d+)', keyword, re.IGNORECASE)
                series_match = re.match(r'^([^\(]+)', keyword)
                series_name  = series_match.group(1).strip() if series_match else keyword
                season   = ep_match.group(1).upper() if ep_match else "S01"
                last_ep  = int(ep_match.group(2)) if ep_match else 1
                last_id  = 0

            # Scan forward to find group-title, and for direct mode: season + last_ep
            group_title = ""
            block_end   = i
            # Track highest single episode seen (for direct mode last_ep detection)
            _highest_ep  = 0
            _highest_seas = ""
            j = i + 1
            while j < len(lines):
                ls = lines[j].strip()
                if re.match(r'^auto_search_update\s*:', ls):
                    break  # next block starts
                gt_match = re.search(r'group-title="([^"]+)"', ls)
                if gt_match and not group_title:
                    group_title = gt_match.group(1)
                # Pick up season/episode from #EXTINF lines (for direct mode)
                if ls.startswith("#EXTINF"):
                    seas_m = re.search(r'(S\d+)E(\d+)(?!\s*[-–]\d)', ls, re.IGNORECASE)
                    if seas_m:
                        ep_num = int(seas_m.group(2))
                        if ep_num > _highest_ep:
                            _highest_ep   = ep_num
                            _highest_seas = seas_m.group(1).upper()
                block_end = j
                j += 1

            # For direct mode fill in season/last_ep from playlist entries
            if is_direct:
                season  = _highest_seas or "S01"
                last_ep = _highest_ep
                # Also derive series_name from group_title if still empty
                if not series_name and group_title:
                    series_name = group_title
            else:
                # Search mode: if playlist entries have a higher episode than URL keyword,
                # use the playlist highest (handles double-episodes like E41-42 correctly)
                if _highest_ep > last_ep:
                    last_ep = _highest_ep
                if _highest_seas:
                    season = _highest_seas

            # Trim block_end: find the last media URL that belongs to THIS
            # group-title (scan from block_end back to block_start).
            # This avoids bleeding into a different group after a blank line.
            real_end = i  # fallback to auto_search_update line itself
            current_group = ""
            for k in range(i + 1, block_end + 1):
                ls = lines[k].strip() if k < len(lines) else ""
                gt_m = re.search(r'group-title="([^"]+)"', ls)
                if gt_m:
                    current_group = gt_m.group(1)
                # Only count lines that belong to the same group-title
                if group_title and current_group and current_group != group_title:
                    continue
                if ls.startswith("#EXTINF") or ls.startswith("#EXTVLCOPT") or re.match(r'https?://', ls):
                    real_end = k

            blocks.append({
                "search_url": search_url,
                "mode": "direct" if is_direct else "search",
                "last_id": last_id,          # used in direct mode
                "series_name": series_name,
                "season": season,
                "last_ep": last_ep,
                "group_title": group_title,
                "block_start": i,
                "block_end": real_end,
            })
        i += 1
    return blocks


def _build_next_search_url(search_url: str, next_ep: int) -> str:
    """Replace the episode number in the search URL with next_ep."""
    def replace_ep(m):
        return m.group(1) + str(next_ep)
    new_url = re.sub(r'(S\d+E)(\d+)', replace_ep, search_url, flags=re.IGNORECASE)
    return new_url


def _search_direct_page(page_url: str, season: str, next_ep: int):
    """
    Fetch a direct listing page (e.g. circleftp) and find the next episode entry.

    Handles:
    - Both zero-padded (S04E01) and non-padded (S4E1) formats in labels/hrefs
    - Season rollover: if next_ep not found in current season, tries S(N+1)E01
    - Multi-season pages (all season tabs present in static HTML)

    Returns dict {href, title, media_url, season} or None if not found.
    """
    try:
        resp = requests.get(page_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Collect all <a> entries with direct .mkv/.mp4 hrefs from the page once
    all_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not re.search(r'\.(mkv|mp4)(\?|$)', href, re.IGNORECASE):
            continue
        label     = (a.get_text(strip=True) or a.get("title", "")).strip()
        parent_tr = a.find_parent("tr")
        row_text  = parent_tr.get_text(" ", strip=True) if parent_tr else ""
        combined  = label + " " + href + " " + row_text
        all_links.append({"href": href, "label": label, "combined": combined})

    def _find_ep(s_num: int, ep_num: int):
        """Search collected links for season s_num episode ep_num."""
        season_pat    = rf'S0*{s_num}'
        ep_pattern    = re.compile(rf'{season_pat}E0*{ep_num}(?!\s*[-–]\d)', re.IGNORECASE)
        range_pattern = re.compile(rf'{season_pat}E\d+[-–]\d+', re.IGNORECASE)
        for link in all_links:
            combined = link["combined"]
            if not ep_pattern.search(combined):
                continue
            if range_pattern.search(combined):
                continue
            title = link["label"] or link["href"].rstrip("/").split("/")[-1]
            return {
                "href": page_url,
                "title": title,
                "media_url": link["href"],
                "season": f"S{s_num:02d}",
            }
        return None

    season_num = int(re.search(r'\d+', season).group())

    # 1. Try the expected next episode in the current season
    result = _find_ep(season_num, next_ep)
    if result:
        return result

    # 2. Season rollover: current season finished, try S(N+1)E01
    result = _find_ep(season_num + 1, 1)
    if result:
        return result

    return None


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
    raw_b64 = data["content"].replace("\n", "").replace("\r", "").strip()
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
    lines = content.splitlines(keepends=True)

    for block in blocks:
        next_ep = block["last_ep"] + 1

        # ════════════════════════════════════════
        #  DIRECT MODE  (e.g. circleftp listing)
        # ════════════════════════════════════════
        if block["mode"] == "direct":
            result = _search_direct_page(
                block["search_url"],
                block["season"],
                next_ep,
            )
            if not result:
                continue

            media_url   = result["media_url"]
            page_url    = block["search_url"]   # referrer = same listing page
            title_label = _make_title_label(result["title"], media_url)
            group_title = block["group_title"] or block["series_name"]

            # Season rollover: result may have a different season than block["season"]
            actual_season = result.get("season", block["season"])
            if actual_season != block["season"]:
                # Reset next_ep to 1 for the new season
                next_ep = 1
                block["season"] = actual_season

            new_entry = _build_m3u_entry(
                group_title=group_title,
                title_label=title_label,
                page_url=page_url,
                media_url=media_url,
                date_str=date_str,
            )

            # Direct mode: auto_search_update URL stays UNCHANGED (same page always)
            # No need to update the auto_search_update line

        # ════════════════════════════════════════
        #  SEARCH MODE  (e.g. fibwatch search)
        # ════════════════════════════════════════
        else:
            next_search_url = _build_next_search_url(block["search_url"], next_ep)

            result = _search_fibwatch(
                next_search_url,
                block["series_name"],
                block["season"],
                next_ep,
            )
            if not result:
                continue

            page_url  = result["href"]
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

            # Search mode: update auto_search_update to next episode number
            new_search_url = _build_next_search_url(block["search_url"], next_ep)
            for li, line in enumerate(lines):
                if line.strip() == f'auto_search_update: "{block["search_url"]}"':
                    lines[li] = f'auto_search_update: "{new_search_url}"\n'
                    break

        # ── Fix 2: Insert new entry right after the last media URL of this block ──
        insert_after = block["block_end"]
        for idx in range(min(block["block_end"], len(lines) - 1), block["block_start"], -1):
            if idx < len(lines) and re.match(r'https?://', lines[idx].strip()):
                insert_after = idx
                break

        if not new_entry.endswith("\n"):
            new_entry += "\n"

        lines.insert(insert_after + 1, new_entry)

        # Shift subsequent block offsets since we inserted lines
        entry_line_count = new_entry.count("\n")
        for b in blocks:
            if b["block_start"] > insert_after:
                b["block_start"] += entry_line_count
                b["block_end"]   += entry_line_count
            elif b["block_end"] > insert_after:
                b["block_end"] += entry_line_count

        added_episodes.append({
            "episode": f"{block['season']}E{next_ep:02d}",
            "title": title_label,
            "media_url": media_url,
        })

    new_content = "".join(lines)

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


def _build_request_headers() -> dict:
    """
    Build headers for an upstream CDN request, using ?referer= / ?ua= query
    params if the client supplies them (e.g. pulled from the M3U's own
    #EXTVLCOPT lines for that specific movie), otherwise falling back to the
    generic fibwatch.art domain-level defaults.
    """
    return {
        "User-Agent": request.args.get('ua') or DEFAULT_USER_AGENT,
        "Referer": request.args.get('referer') or DEFAULT_REFERER,
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


@app.route('/debug-github', methods=['GET'])
def debug_github_endpoint():
    """
    Isolates GitHub connectivity from the rest of the CDN-sync flow, so you
    can tell in one request whether the problem is:
      - GITHUB_TOKEN missing/not loaded in this deployment
      - token present but invalid / expired / wrong repo access
      - token valid but missing 'Contents: Read and write' permission
      - or none of the above (in which case the problem is upstream —
        the dead-link detection never fired, see /sync-cdn instead)
    Token itself is never returned, only masked info + GitHub's own verdict.
    """
    token_present = bool(GITHUB_TOKEN)
    token_preview = f"{GITHUB_TOKEN[:8]}...{GITHUB_TOKEN[-4:]}" if token_present and len(GITHUB_TOKEN) > 12 else None

    out = {
        "github_token_env_var_present": token_present,
        "token_preview": token_preview,
        "target_repo": GITHUB_REPO,
        "target_branch": GITHUB_BRANCH,
        "target_file": GITHUB_FILE,
    }

    if not token_present:
        out["verdict"] = ("GITHUB_TOKEN not visible to this running deployment. "
                           "Set it in Vercel Project Settings -> Environment Variables "
                           "for the Production environment (and Preview, if you test preview "
                           "URLs), then REDEPLOY — Vercel does not hot-reload env vars into "
                           "already-running deployments.")
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=500, mimetype='application/json'))

    # 1) Can we even authenticate?
    gh_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        user_resp = requests.get("https://api.github.com/user", headers=gh_headers, timeout=10)
    except Exception as e:
        out["verdict"] = f"Network error reaching GitHub API: {e}"
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=502, mimetype='application/json'))

    out["auth_status_code"] = user_resp.status_code
    if user_resp.status_code == 401:
        out["verdict"] = "Token is invalid/expired/revoked (401 Unauthorized). Generate a new token."
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=500, mimetype='application/json'))
    out["authenticated_as"] = user_resp.json().get("login") if user_resp.status_code == 200 else None

    # 2) Can we read the target file with this token?
    file_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    try:
        get_resp = requests.get(file_url, headers=gh_headers, params={"ref": GITHUB_BRANCH}, timeout=10)
    except Exception as e:
        out["verdict"] = f"Network error reading target file: {e}"
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=502, mimetype='application/json'))

    out["read_status_code"] = get_resp.status_code
    if get_resp.status_code == 404:
        out["verdict"] = (f"404 on {GITHUB_REPO}/{GITHUB_FILE} — either the repo/file/branch name is wrong, "
                           f"or (for a fine-grained token) this repo isn't in the token's repository access list.")
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=500, mimetype='application/json'))
    if get_resp.status_code != 200:
        out["verdict"] = f"Unexpected error reading file: {get_resp.status_code} {get_resp.text[:300]}"
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=500, mimetype='application/json'))

    sha = get_resp.json().get("sha")
    out["current_sha"] = sha

    # 3) Can we actually WRITE? Do a harmless no-op PUT (same content, same sha)
    #    so it doesn't create a real commit unless GitHub rejects for permission reasons.
    raw_b64 = get_resp.json()["content"].replace("\n", "").replace("\r", "").strip()
    current_content = base64.b64decode(raw_b64)
    test_headers = {**gh_headers, "Content-Type": "application/json"}
    put_payload = {
        "message": "debug-github: permission check (no content change)",
        "content": base64.b64encode(current_content).decode("utf-8"),
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    try:
        put_resp = requests.put(file_url, headers=test_headers, json=put_payload, timeout=15)
    except Exception as e:
        out["verdict"] = f"Network error during write test: {e}"
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=502, mimetype='application/json'))

    out["write_status_code"] = put_resp.status_code
    if put_resp.status_code in (200, 201):
        out["verdict"] = "Everything works — token can read AND write. A real sync SHOULD succeed. If it still doesn't, the problem is upstream (dead-link detection never firing) — check /sync-cdn and Vercel Runtime Logs for '[cdn-sync]' lines."
        return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=200, mimetype='application/json'))
    elif put_resp.status_code == 403:
        out["verdict"] = ("403 Forbidden on write — token can READ but not WRITE. For a fine-grained PAT: "
                           "go to the token's settings and set 'Contents' permission to 'Read and write' "
                           "for this repo. For a classic PAT: it needs the full 'repo' scope.")
        out["github_response"] = put_resp.text[:500]
    elif put_resp.status_code == 404:
        out["verdict"] = "404 on write — same repo-access issue as the read check above."
        out["github_response"] = put_resp.text[:500]
    elif put_resp.status_code == 409:
        out["verdict"] = "409 Conflict — sha changed between read and write (something else committed in between). Not a permission issue; try again."
    else:
        out["verdict"] = f"Unexpected write failure: {put_resp.status_code}"
        out["github_response"] = put_resp.text[:500]

    return cors_headers(Response(json.dumps(out, ensure_ascii=False, indent=2), status=500, mimetype='application/json'))


@app.route('/sync-cdn', methods=['GET'])
def sync_cdn_endpoint():
    """
    Manual/debug trigger — force-resolve the current active CDN domain and
    push a full playlist-wide domain sync to GitHub right now (bypasses the
    cooldown). Useful for testing without needing a dead link first.
    """
    domain = get_active_cdn_domain(force=True)
    if not domain:
        return cors_headers(
            Response(json.dumps({"status": "error", "message": "Could not resolve active CDN domain"}),
                     status=502, mimetype='application/json')
        )
    result = sync_playlist_cdn_domain(domain)
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

    req_headers = _build_request_headers()
    video_url = resolve_working_url(video_url, req_headers)

    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'quiet',
                '-print_format', 'json',
                '-show_streams',
                '-select_streams', 'a',
                '-headers', f'User-Agent: {req_headers["User-Agent"]}\r\nReferer: {req_headers["Referer"]}\r\n',
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


# ─────────────────────────────────────────────
#  HTTP Referrer Auto-Fixer
# ─────────────────────────────────────────────

def clean_title_for_search(extinf_line: str) -> str:
    parts = extinf_line.split(',', 1)
    if len(parts) > 1:
        title = parts[1].strip()
    else:
        title = extinf_line.strip()
    t = re.sub(r'\[(?i:Fibwatch\.Com)\]', '', title)
    t = re.sub(r'(?i)1080p|720p|480p', '', t)
    t = re.sub(r'(?i)\bDual\b', '', t)
    t = re.sub(r'(?i)\bAudio\b', '', t)
    t = re.sub(r'(?i)\bHQ\b', '', t)
    t = re.sub(r'(?i)\bWEB-DL\b', '', t)
    t = t.replace('.', ' ').replace('-', ' ')
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def get_new_referrer(cleaned_title: str, original_title: str) -> tuple[str | None, str | None, str]:
    # Step 1: Search movie
    search_url = f"https://fibwatch.art/search?keyword={urllib.parse.quote(cleaned_title)}"
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200: return None, None, f"Search failed: HTTP {resp.status_code}"
        html = resp.text
    except Exception as e:
        return None, None, f"Search error: {str(e)}"

    # Step 2: Pick best match
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    target_res = '1080' if '1080' in original_title else ('720' if '720' in original_title else ('480' if '480' in original_title else ''))
    has_hindi = 'hindi' in original_title.lower()
    has_bangla = 'bangla' in original_title.lower() or 'bengali' in original_title.lower()

    for a in soup.find_all("a", href=True):
        title_text = (a.get_text(strip=True) or a.get("title", "")).strip()
        href = a["href"]
        if not title_text: continue
        if href.startswith("/"): href = "https://fibwatch.art" + href
        c_res = '1080' if '1080' in title_text else ('720' if '720' in title_text else ('480' if '480' in title_text else ''))
        candidates.append({
            "title": title_text,
            "href": href,
            "res": c_res,
            "hindi": 'hindi' in title_text.lower(),
            "bangla": 'bangla' in title_text.lower() or 'bengali' in title_text.lower()
        })

    if not candidates: return None, None, "No candidates found"
    res_filtered = [c for c in candidates if c["res"] == target_res] if target_res else candidates
    if not res_filtered: res_filtered = candidates
    lang_filtered = [c for c in res_filtered if c["hindi"]] if has_hindi else ([c for c in res_filtered if c["bangla"]] if has_bangla else res_filtered)
    if not lang_filtered: lang_filtered = res_filtered
    single_page_url = lang_filtered[0]["href"]

    # Step 3: Fetch single page and extract live CDN video URL
    try:
        resp2 = requests.get(single_page_url, headers=HEADERS, timeout=10)
        if resp2.status_code != 200: return None, None, f"Single page failed: HTTP {resp2.status_code}"
        html2 = resp2.text
    except Exception as e:
        return None, None, f"Single page error: {str(e)}"

    # Extract JS variables
    video_url_match = re.search(r'var\s+VIDEO_URL\s*=\s*[\'"]([^\'"]+)[\'"]', html2)
    
    if not video_url_match:
        return None, None, "JS variables not found"
        
    video_url = video_url_match.group(1)
    
    # Try to generate new referer via API (since some movies require the shortlink)
    api_url = shortlink_base.replace('/st?api=', '/api?api=') + urllib.parse.quote(video_url)
    try:
        resp3 = requests.get(api_url, headers=HEADERS, timeout=15)
        if resp3.status_code == 200:
            data = resp3.json()
            if data.get('status') == 'success':
                alias = data['shortenedUrl'].split('/')[-1]
                return f"https://urlshortlink.top/{alias}", video_url, "success"
    except Exception:
        pass
    
    # Fallback: Fibwatch URL works as referer for most movies if the API fails
    return single_page_url, video_url, "success (fallback)"


@app.route('/fix-referrers', methods=['GET', 'POST'])
def fix_referrers_endpoint():
    """
    Checks Fibwatch movies for dead HTTP referers and dynamically resolves new ones using Fibwatch search.
    Due to serverless timeouts, fixes max 3 referers per request.
    """
    try:
        content, sha = _github_get_file()
    except Exception as e:
        return cors_headers(Response(json.dumps({"error": str(e)}), status=500, mimetype='application/json'))

    lines = content.splitlines(keepends=True)
    blocks_to_check = []

    # Parse M3U into blocks for fast checking
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("#EXTINF") and "fibwatch.com" in line.lower():
            start_idx = i
            j = i + 1
            referer_idx = -1
            video_idx = -1
            while j < len(lines):
                next_line = lines[j]
                if next_line.strip().startswith("#EXTVLCOPT:http-referrer="):
                    referer_idx = j
                elif next_line.strip().startswith("http") and not next_line.strip().startswith("#"):
                    video_idx = j
                    break
                elif next_line.strip().startswith("#EXTINF"):
                    break
                j += 1
            if video_idx != -1 and referer_idx != -1:
                title = line.strip()
                current_referer = lines[referer_idx].strip().split("=", 1)[1]
                video_url = lines[video_idx].strip()
                blocks_to_check.append({
                    "title": title,
                    "referer": current_referer,
                    "video_url": video_url,
                    "referer_idx": referer_idx,
                    "video_idx": video_idx
                })
            i = j
        else:
            i += 1

    # Check reachability concurrently (very fast)
    failed_blocks = []
    def check_reach(b):
        if not _is_url_reachable(b["video_url"], {"Referer": b["referer"], "User-Agent": DEFAULT_USER_AGENT}):
            return b
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = executor.map(check_reach, blocks_to_check)
        for r in results:
            if r is not None:
                failed_blocks.append(r)

    if not failed_blocks:
        return cors_headers(Response(json.dumps({"status": "ok", "message": "All referers are working"}), status=200, mimetype='application/json'))

    changed = False
    updates_count = 0
    fixed_titles = []
    
    # Process fixes sequentially because it requires time.sleep() and multiple page loads.
    # With the new fast API logic, we can process up to 10 fixes per request.
    debug_logs = {}
    for block in failed_blocks[:10]:
        cleaned = clean_title_for_search(block["title"])
        new_ref, new_video, debug_msg = get_new_referrer(cleaned, block["title"])
        if new_ref and new_video:
            if new_ref != block["referer"] or new_video != block["video_url"]:
                lines[block["referer_idx"]] = f"#EXTVLCOPT:http-referrer={new_ref}\n"
                lines[block["video_idx"]] = f"{new_video}\n"
                changed = True
                updates_count += 1
                fixed_titles.append(cleaned)
            else:
                debug_logs[cleaned] = "Link already up to date, but unreachable."
        else:
            debug_logs[cleaned] = debug_msg

    has_more = len(failed_blocks) > 10

    if changed:
        new_content = "".join(lines)
        commit_msg = f"Auto referer sync: Fixed {updates_count} links"
        try:
            _github_push_file(new_content, sha, commit_msg)
        except Exception as e:
            return cors_headers(Response(json.dumps({"error": f"GitHub push failed: {e}"}), status=500, mimetype='application/json'))

    return cors_headers(Response(json.dumps({
        "status": "ok",
        "fixed": updates_count,
        "titles": fixed_titles,
        "total_failed_remaining": len(failed_blocks) - updates_count,
        "has_more": has_more,
        "debug_logs": debug_logs
    }, ensure_ascii=False), status=200, mimetype='application/json'))


@app.route('/proxy', methods=['GET', 'OPTIONS'])
def proxy_video():
    if request.method == 'OPTIONS':
        return cors_headers(Response('', status=204))

    video_url = request.args.get('url', '')
    if not video_url:
        return cors_headers(Response('Missing url parameter', status=400))
    if not re.match(r'https?://', video_url):
        return cors_headers(Response('Invalid URL', status=400))

    req_headers = _build_request_headers()

    # Auto-fix dead/rotated CDN domain before we try to stream this URL
    video_url = resolve_working_url(video_url, req_headers)

    audio_track = request.args.get('audio', None)

    if audio_track is not None:
        try:
            track_idx = int(audio_track)
        except ValueError:
            track_idx = 0

        def generate_ffmpeg():
            cmd = [
                'ffmpeg', '-v', 'quiet',
                '-headers', f'User-Agent: {req_headers["User-Agent"]}\r\nReferer: {req_headers["Referer"]}\r\n',
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