#!/usr/bin/env python3
"""
Fibwatch M3U Auto-Updater — Smart Domain-Only Replace
======================================================
- শুধু [Fibwatch.Com] থাকা media URL-এর domain বদলায়
- Path/filename হুবহু একই থাকে
- অন্য সব entries (circleftp, ftpbd, etc.) কখনোই touch করে না
- M3U-তে যত Fibwatch entry আছে, সব automatically detect করে

Setup:
  pip install requests beautifulsoup4 PyGithub schedule

Environment Variables:
  GITHUB_TOKEN    GitHub Personal Access Token (repo write permission)
  GITHUB_REPO     e.g. "username/myrepo"
  M3U_FILE_PATH   e.g. "playlist.m3u"
  GITHUB_BRANCH   e.g. "main"  (default: main)
"""

import re
import os
import time
import logging
import schedule
import requests
from bs4 import BeautifulSoup
from github import Github, GithubException
from datetime import datetime
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
CONFIG = {
    "github_token":  os.getenv("GITHUB_TOKEN",  "YOUR_GITHUB_TOKEN_HERE"),
    "github_repo":   os.getenv("GITHUB_REPO",   "YOUR_USERNAME/YOUR_REPO"),
    "m3u_file_path": os.getenv("M3U_FILE_PATH", "playlist.m3u"),
    "branch":        os.getenv("GITHUB_BRANCH", "main"),

    # রাত ১২টায় run করবে (Bangladesh time হলে server timezone অনুযায়ী adjust করো)
    "run_at": "00:00",
}

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://fibwatch.art/",
}

# ─────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("fibwatch_updater.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  Step 1: M3U থেকে সব Fibwatch entries automatically scan করা
# ─────────────────────────────────────────────────────────────
def scan_fibwatch_entries(m3u_text: str) -> list[dict]:
    """
    M3U file parse করে শুধু [Fibwatch.Com] থাকা entries বের করে।
    
    Returns list of dicts:
      {
        "referrer_url":    "https://fibwatch.art/watch/...",
        "current_url":     "https://OLD-DOMAIN.b-cdn.net/s3/upload/.../[Fibwatch.Com]....mkv",
        "current_domain":  "OLD-DOMAIN.b-cdn.net",
        "path_after_domain": "/s3/upload/.../[Fibwatch.Com]....mkv",
      }
    """
    entries = []
    lines = m3u_text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Fibwatch referrer line খোঁজো
        if line.startswith("#EXTVLCOPT:http-referrer=") and "fibwatch.art/watch/" in line:
            referrer_url = line.split("=", 1)[1].strip()

            # পরের কয়েকটা line-এ media URL খোঁজো (# দিয়ে শুরু নয় এমন URL)
            j = i + 1
            while j < len(lines):
                candidate = lines[j].strip()
                if candidate.startswith("#"):
                    j += 1
                    continue
                # URL এবং [Fibwatch.Com] আছে কিনা দেখো
                if re.search(r'\[Fibwatch\.Com\]', candidate, re.IGNORECASE) and \
                   re.search(r'\.(mkv|mp4)', candidate, re.IGNORECASE):
                    parsed = urlparse(candidate)
                    entries.append({
                        "referrer_url":      referrer_url,
                        "current_url":       candidate,
                        "current_domain":    parsed.netloc,
                        "path_after_domain": parsed.path + (
                            ("?" + parsed.query) if parsed.query else ""
                        ),
                        "line_index":        j,
                    })
                break  # প্রথম non-# line-ই media URL
                j += 1

        i += 1

    return entries


# ─────────────────────────────────────────────────────────────
#  Step 2: fibwatch page থেকে current media URL ও domain বের করা
# ─────────────────────────────────────────────────────────────
def fetch_current_domain(watch_url: str) -> tuple[str, str] | tuple[None, None]:
    """
    fibwatch watch page থেকে .mkv/.mp4 link এবং তার domain বের করে।
    Returns: (full_media_url, domain)  or  (None, None)
    """
    log.info(f"  Fetching: {watch_url}")
    try:
        resp = requests.get(watch_url, headers=FETCH_HEADERS, timeout=25)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"  ✗ Fetch failed: {e}")
        return None, None

    html = resp.text

    # Method 1: HTML tag (<source>, <video>, <a>)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["source", "video", "a"]):
        for attr in ("src", "href", "data-src", "data-url"):
            url = (tag.get(attr) or "").strip()
            if re.search(r'\[Fibwatch\.Com\]', url, re.IGNORECASE) and \
               re.search(r'\.(mkv|mp4)', url, re.IGNORECASE):
                domain = urlparse(url).netloc
                log.info(f"  ✓ Found via HTML tag: domain={domain}")
                return url, domain

    # Method 2: Regex — [Fibwatch.Com] সহ .mkv/.mp4 URL
    pattern = r'https?://[^\s\'"<>]*\[Fibwatch\.Com\][^\s\'"<>]*\.(?:mkv|mp4)(?:\?[^\s\'"<>]*)?'
    matches = re.findall(pattern, html, re.IGNORECASE)
    if matches:
        url = matches[0].strip()
        domain = urlparse(url).netloc
        log.info(f"  ✓ Found via regex: domain={domain}")
        return url, domain

    # Method 3: যেকোনো b-cdn.net বা CDN URL
    pattern2 = r'https?://[^\s\'"<>]+\.b-cdn\.net/[^\s\'"<>]+\.(?:mkv|mp4)(?:\?[^\s\'"<>]*)?'
    matches2 = re.findall(pattern2, html, re.IGNORECASE)
    if matches2:
        url = matches2[0].strip()
        domain = urlparse(url).netloc
        log.info(f"  ✓ Found via CDN regex: domain={domain}")
        return url, domain

    log.warning(f"  ✗ No media URL found on page.")
    return None, None


# ─────────────────────────────────────────────────────────────
#  Step 3: M3U content আপডেট করা — শুধু domain replace
# ─────────────────────────────────────────────────────────────
def update_m3u_domains(m3u_text: str, entries: list[dict], domain_map: dict) -> tuple[str, int]:
    """
    entries-এর প্রতিটার জন্য domain_map অনুযায়ী domain replace করে।
    domain_map = { "old_domain": "new_domain", ... }
    
    Returns: (updated_m3u_text, change_count)
    """
    lines = m3u_text.splitlines(keepends=True)
    change_count = 0

    for entry in entries:
        old_domain = entry["current_domain"]
        new_domain = domain_map.get(old_domain)

        if not new_domain:
            log.info(f"  No new domain found for: {old_domain} — skipping")
            continue

        if old_domain == new_domain:
            log.info(f"  Domain unchanged: {old_domain}")
            continue

        line_idx = entry["line_index"]
        old_line = lines[line_idx]
        # শুধু domain অংশটা replace করো, বাকি সব (path, filename) অপরিবর্তিত
        new_line = old_line.replace(old_domain, new_domain, 1)

        if new_line != old_line:
            log.info(f"  CHANGED line {line_idx + 1}:")
            log.info(f"    OLD domain: {old_domain}")
            log.info(f"    NEW domain: {new_domain}")
            lines[line_idx] = new_line
            change_count += 1

    return "".join(lines), change_count


# ─────────────────────────────────────────────────────────────
#  GitHub read / write
# ─────────────────────────────────────────────────────────────
def github_read_m3u(gh: Github, repo_name: str, file_path: str, branch: str):
    repo = gh.get_repo(repo_name)
    contents = repo.get_contents(file_path, ref=branch)
    text = contents.decoded_content.decode("utf-8")
    return repo, contents.sha, text


def github_write_m3u(repo, file_path: str, content: str, sha: str, branch: str):
    msg = f"[Auto] Update Fibwatch CDN domains [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
    repo.update_file(path=file_path, message=msg, content=content, sha=sha, branch=branch)
    log.info(f"  ✓ Committed: {msg}")


# ─────────────────────────────────────────────────────────────
#  Main Job
# ─────────────────────────────────────────────────────────────
def run_update_job():
    log.info("=" * 65)
    log.info(f"Job started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. GitHub থেকে M3U পড়ো
    gh = Github(CONFIG["github_token"])
    try:
        repo, sha, m3u_text = github_read_m3u(
            gh, CONFIG["github_repo"], CONFIG["m3u_file_path"], CONFIG["branch"]
        )
        log.info(f"✓ Read {CONFIG['m3u_file_path']} from GitHub ({len(m3u_text)} chars)")
    except GithubException as e:
        log.error(f"✗ Cannot read from GitHub: {e}")
        return

    # 2. M3U থেকে সব Fibwatch entries scan করো
    entries = scan_fibwatch_entries(m3u_text)
    if not entries:
        log.info("No Fibwatch entries found in M3U. Nothing to do.")
        return
    log.info(f"Found {len(entries)} Fibwatch entry/entries in M3U:")
    for e in entries:
        log.info(f"  • {e['referrer_url']}  →  current domain: {e['current_domain']}")

    # 3. প্রতিটা fibwatch page fetch করে নতুন domain বের করো
    #    একই domain একাধিক entry-তে থাকলে একবারই fetch করো
    unique_pages: dict[str, str] = {}  # referrer_url → new_domain
    for entry in entries:
        watch_url = entry["referrer_url"]
        if watch_url in unique_pages:
            continue
        _, new_domain = fetch_current_domain(watch_url)
        unique_pages[watch_url] = new_domain  # None হলেও store করো

    # domain_map তৈরি: old_domain → new_domain
    # (একটা entry-তে referrer_url ↔ current_domain আছে)
    domain_map: dict[str, str] = {}
    for entry in entries:
        new_domain = unique_pages.get(entry["referrer_url"])
        if new_domain:
            domain_map[entry["current_domain"]] = new_domain

    if not domain_map:
        log.warning("Could not fetch any new domain. Aborting.")
        return

    log.info("Domain mapping:")
    for old, new in domain_map.items():
        log.info(f"  {old}  →  {new}")

    # 4. M3U আপডেট করো
    updated_text, change_count = update_m3u_domains(m3u_text, entries, domain_map)

    if change_count == 0:
        log.info("ℹ️  All domains already up-to-date. No commit needed.")
    else:
        try:
            github_write_m3u(repo, CONFIG["m3u_file_path"], updated_text, sha, CONFIG["branch"])
            log.info(f"✅ {change_count} line(s) updated on GitHub.")
        except GithubException as e:
            log.error(f"✗ GitHub write failed: {e}")

    log.info("Job finished.")
    log.info("=" * 65)


# ─────────────────────────────────────────────────────────────
#  Scheduler
# ─────────────────────────────────────────────────────────────
def main():
    log.info(f"Scheduler ready — daily job at {CONFIG['run_at']}")
    log.info(f"Repo: {CONFIG['github_repo']}  |  File: {CONFIG['m3u_file_path']}")

    schedule.every().day.at(CONFIG["run_at"]).do(run_update_job)

    # প্রথমবার এখনই run করতে নিচের লাইন uncomment করো:
    # run_update_job()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
