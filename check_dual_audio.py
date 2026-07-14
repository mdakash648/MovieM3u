import re
import requests

def check_audio_tracks(stream_url, timeout=8, verbose=False):
    """
    HLS master playlist (m3u8) সরাসরি fetch করে EXT-X-MEDIA:TYPE=AUDIO
    লাইনগুলো পার্স করে ইউনিক audio track সংখ্যা বের করে।
    এটা ffprobe এর চেয়ে বেশি নির্ভরযোগ্য কারণ ffprobe শুধু একটা
    variant প্রোব করে, পুরো manifest এর সব audio group না।

    Returns: (count, details)
        count -> ইউনিক audio track সংখ্যা (int)
        details -> [{"group_id":..., "name":..., "language":..., "default":...}, ...]
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(stream_url, timeout=timeout, headers=headers)
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        if verbose:
            print(f"    [ERROR fetching {stream_url}] {type(e).__name__}: {e}")
        return 0, []

    if "#EXT-X-MEDIA" not in text:
        if verbose:
            snippet = text[:150].replace("\n", " | ")
            print(f"    [no EXT-X-MEDIA in manifest] {stream_url} -> starts with: {snippet}")
        return 0, []

    audio_lines = [
        line for line in text.splitlines()
        if line.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in line
    ]

    seen = set()
    details = []
    for line in audio_lines:
        def attr(name):
            m = re.search(rf'{name}="([^"]*)"', line)
            return m.group(1) if m else None

        group_id = attr("GROUP-ID")
        name = attr("NAME")
        language = attr("LANGUAGE")
        default = "DEFAULT=YES" in line

        # ইউনিক কি হবে group_id + name (একই group এ ভিন্ন নামের একাধিক ভাষা থাকতে পারে)
        key = (group_id, name, language)
        if key not in seen:
            seen.add(key)
            details.append({
                "group_id": group_id,
                "name": name,
                "language": language,
                "default": default,
            })

    return len(details), details


def load_playlist_text(m3u_path):
    """লোকাল ফাইল অথবা URL — দুটোই সাপোর্ট করে"""
    if m3u_path.startswith("http://") or m3u_path.startswith("https://"):
        resp = requests.get(m3u_path, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def parse_playlist(text):
    """
    M3U টেক্সট পার্স করে চ্যানেল লিস্ট বানায়।
    একটা #EXTINF এর নিচে একাধিক fallback URL থাকতে পারে —
    সবগুলোকে একই চ্যানেলের সাথে group করা হয়।
    কমেন্ট করা URL (# দিয়ে শুরু, যেমন #https://...) বাদ দেওয়া হয়।
    """
    channels = []  # [{"name": ..., "urls": [url1, url2, ...]}]
    current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            parts = line.split(",", 1)
            name = parts[1].strip() if len(parts) > 1 else "Unknown"
            current = {"name": name, "urls": []}
            channels.append(current)
        elif line.startswith("#"):
            continue  # অন্যান্য কমেন্ট/মেটাডেটা/ডিজেবলড লাইন — স্কিপ
        else:
            if current is None:
                current = {"name": "Unknown", "urls": []}
                channels.append(current)
            current["urls"].append(line)

    return channels


def scan_playlist(m3u_path, check_all_fallbacks=False, limit=None, verbose=False):
    """
    একটি m3u প্লেলিস্ট (ফাইল বা URL) থেকে সব চ্যানেল বের করে,
    প্রতিটা চ্যানেলের audio track সংখ্যা চেক করে,
    এবং যেগুলোতে dual/multi audio আছে তাদের লিস্ট রিটার্ন করে।

    check_all_fallbacks=False হলে প্রতি চ্যানেলের শুধু প্রথম URL চেক হয় (দ্রুত)।
    True হলে প্রথম URL এ কাজ না করলে পরেরগুলোও ট্রাই করা হয় (ধীর কিন্তু নির্ভুল)।
    """
    text = load_playlist_text(m3u_path)
    channels = parse_playlist(text)
    if limit:
        channels = channels[:limit]

    print(f"মোট {len(channels)} টি চ্যানেল পাওয়া গেছে। চেক শুরু হচ্ছে...\n")

    dual_audio_channels = []
    no_audio_info = []

    for i, ch in enumerate(channels, 1):
        name = ch["name"]
        urls = ch["urls"]
        if not urls:
            continue

        urls_to_try = urls if check_all_fallbacks else urls[:1]
        found = False
        for url in urls_to_try:
            count, details = check_audio_tracks(url, verbose=verbose)
            if count > 0:
                found = True
                langs = [d.get("language") or d.get("name") or "?" for d in details]
                status = "[DUAL/MULTI AUDIO]" if count > 1 else "[single audio]"
                print(f"{i}/{len(channels)} {status} {name} -> {count} track(s) -> {langs}")
                if count > 1:
                    dual_audio_channels.append((name, url, count, langs))
                break
        if not found:
            no_audio_info.append((name, urls[0]))
            print(f"{i}/{len(channels)} [no audio-tag info / manifest not readable] {name}")

    print(f"\n===== ফলাফল =====")
    print(f"Dual/Multi audio চ্যানেল: {len(dual_audio_channels)}")
    for name, url, count, langs in dual_audio_channels:
        print(f"  - {name}: {count} tracks ({langs})")
    print(f"\nAudio info পাওয়া যায়নি এমন চ্যানেল: {len(no_audio_info)}")

    return dual_audio_channels, no_audio_info


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print('Usage: python check_dual_audio.py "<playlist.m3u or URL>" [--all-fallbacks] [--limit N] [--verbose]')
        sys.exit(1)

    path = sys.argv[1]
    check_all = "--all-fallbacks" in sys.argv
    verbose = "--verbose" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit = int(sys.argv[idx + 1])

    scan_playlist(path, check_all_fallbacks=check_all, limit=limit, verbose=verbose)