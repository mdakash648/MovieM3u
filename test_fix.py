import sys
sys.path.append('api')
from index import *

content, sha = _github_get_file()
lines = content.splitlines(keepends=True)
blocks_to_check = []
i = 0
while i < len(lines):
    line = lines[i]
    if line.strip().startswith("#EXTINF") and "fibwatch.com" in line.lower():
        j = i + 1
        referer_idx = -1
        video_idx = -1
        while j < len(lines):
            next_line = lines[j]
            if next_line.strip().startswith("#EXTVLCOPT:http-referrer="): referer_idx = j
            elif next_line.strip().startswith("http") and not next_line.strip().startswith("#"):
                video_idx = j
                break
            elif next_line.strip().startswith("#EXTINF"): break
            j += 1
        if video_idx != -1 and referer_idx != -1:
            title = line.strip()
            current_referer = lines[referer_idx].strip().split("=", 1)[1]
            video_url = lines[video_idx].strip()
            if not _is_url_reachable(video_url, {"Referer": current_referer, "User-Agent": DEFAULT_USER_AGENT}):
                blocks_to_check.append({"title": title, "referer": current_referer})
        i = j
    else:
        i += 1

print(f"Failed blocks: {len(blocks_to_check)}")
for block in blocks_to_check[:2]:
    title = block["title"]
    print(f"Testing: {title}")
    cleaned = clean_title_for_search(title)
    print(f"Cleaned: {cleaned}")
    search_url = f"https://fibwatch.art/search?keyword={urllib.parse.quote(cleaned)}"
    print(f"Search URL: {search_url}")
    resp = requests.get(search_url, headers=HEADERS, timeout=10)
    print(f"Search Status: {resp.status_code}")
    soup = BeautifulSoup(resp.text, "html.parser")
    candidates = []
    target_res = '1080' if '1080' in title else ('720' if '720' in title else ('480' if '480' in title else ''))
    has_hindi = 'hindi' in title.lower()
    has_bangla = 'bangla' in title.lower() or 'bengali' in title.lower()
    for a in soup.find_all("a", href=True):
        title_text = (a.get_text(strip=True) or a.get("title", "")).strip()
        href = a["href"]
        if not title_text: continue
        if href.startswith("/"): href = "https://fibwatch.art" + href
        c_res = '1080' if '1080' in title_text else ('720' if '720' in title_text else ('480' if '480' in title_text else ''))
        candidates.append({"title": title_text, "href": href, "res": c_res, "hindi": 'hindi' in title_text.lower(), "bangla": 'bangla' in title_text.lower()})
    
    if not candidates:
        print("No candidates found")
        continue
        
    res_filtered = [c for c in candidates if c["res"] == target_res] if target_res else candidates
    if not res_filtered: res_filtered = candidates
    lang_filtered = [c for c in res_filtered if c["hindi"]] if has_hindi else ([c for c in res_filtered if c["bangla"]] if has_bangla else res_filtered)
    if not lang_filtered: lang_filtered = res_filtered
    single_page_url = lang_filtered[0]["href"]
    print(f"Selected Page: {single_page_url}")
    
    resp2 = requests.get(single_page_url, headers=HEADERS, timeout=10)
    print(f"Page Status: {resp2.status_code}")
    soup2 = BeautifulSoup(resp2.text, "html.parser")
    download_url = None
    btn = soup2.find("a", id="fwDownloadBtn")
    if btn: download_url = btn.get("href")
    if not download_url:
        for a in soup2.find_all("a", href=True):
            if "download" in a.get_text(strip=True).lower():
                download_url = a["href"]
                break
    print(f"Download URL: {download_url}")
    if download_url and download_url.startswith("/"): download_url = "https://fibwatch.art" + download_url
    if not download_url: continue
    
    resp3 = requests.get(download_url, headers=HEADERS, timeout=15)
    print(f"Download Page Status: {resp3.status_code}")
    soup3 = BeautifulSoup(resp3.text, "html.parser")
    safelink_redirect_url = None
    wpsafe = soup3.find(id="wpsafe-link")
    if wpsafe:
        a_tag = wpsafe.find("a", href=True)
        if a_tag: safelink_redirect_url = a_tag["href"]
    print(f"Safelink Redirect URL: {safelink_redirect_url}")
