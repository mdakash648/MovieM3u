import sys
import urllib.parse
import requests
from bs4 import BeautifulSoup
sys.path.append('api')
from index import *

title = 'KARIKAADA.2026.Dual.1080p [Fibwatch.Com]'
cleaned = clean_title_for_search(title)
print(f'Clean: {cleaned}')
search_url = f"https://fibwatch.art/search?keyword={urllib.parse.quote(cleaned)}"
print(f"Search URL: {search_url}")

resp = requests.get(search_url, headers=HEADERS, timeout=10)
print(f"Search Status: {resp.status_code}")
soup = BeautifulSoup(resp.text, "html.parser")
candidates = []
target_res = '1080' if '1080' in title else ('720' if '720' in title else ('480' if '480' in title else ''))
print(f"Target Res: {target_res}")
for a in soup.find_all("a", href=True):
    title_text = (a.get_text(strip=True) or a.get("title", "")).strip()
    href = a["href"]
    if not title_text: continue
    if href.startswith("/"): href = "https://fibwatch.art" + href
    c_res = '1080' if '1080' in title_text else ('720' if '720' in title_text else ('480' if '480' in title_text else ''))
    if "KARIKAADA" in title_text.upper():
        print(f"Found Candidate: {title_text} - {href}")
    candidates.append({"title": title_text, "href": href, "res": c_res, "hindi": 'hindi' in title_text.lower(), "bangla": 'bangla' in title_text.lower()})

if not candidates:
    print("NO CANDIDATES")
    sys.exit(1)

res_filtered = [c for c in candidates if c["res"] == target_res] if target_res else candidates
if not res_filtered: res_filtered = candidates
lang_filtered = [c for c in res_filtered if c["hindi"]]
if not lang_filtered: lang_filtered = res_filtered
single_page_url = lang_filtered[0]["href"]
print(f"Selected Page: {single_page_url}")

resp2 = requests.get(single_page_url, headers=HEADERS, timeout=10)
print(f"Page Status: {resp2.status_code}")
soup2 = BeautifulSoup(resp2.text, "html.parser")
download_url = None
btn = soup2.find("a", id="fwDownloadBtn")
if btn: 
    download_url = btn.get("href")
    print("Found via id='fwDownloadBtn'")
if not download_url:
    for a in soup2.find_all("a", href=True):
        if "download" in a.get_text(strip=True).lower():
            download_url = a["href"]
            print(f"Found via 'download' text: {download_url}")
            break
print(f"Download URL: {download_url}")
if download_url and download_url.startswith("/"): download_url = "https://fibwatch.art" + download_url

if not download_url:
    print("NO DOWNLOAD URL")
    sys.exit(1)

resp3 = requests.get(download_url, headers=HEADERS, timeout=15)
print(f"Download Page Status: {resp3.status_code}")
soup3 = BeautifulSoup(resp3.text, "html.parser")
safelink_redirect_url = None
wpsafe = soup3.find(id="wpsafe-link")
if wpsafe:
    a_tag = wpsafe.find("a", href=True)
    if a_tag: safelink_redirect_url = a_tag["href"]
print(f"Safelink Redirect URL: {safelink_redirect_url}")

if safelink_redirect_url:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(safelink_redirect_url).query)
    print(f"QS: {qs}")
    if "safelink_redirect" in qs:
        b64_str = qs["safelink_redirect"][0]
        padding = '=' * (4 - len(b64_str) % 4)
        data = json.loads(base64.b64decode(b64_str + padding).decode())
        safelink = data.get("safelink")
        print(f"Final Safelink: {safelink}")
