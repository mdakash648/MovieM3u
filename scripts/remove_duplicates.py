import re

file_path = 'Live tv.m3u'

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
except UnicodeDecodeError:
    with open(file_path, 'r', encoding='latin-1') as f:
        lines = f.readlines()

header = []
entries = []
current_entry = []
header_done = False

# Step 1: Parse the file into header and entries
for line in lines:
    if line.startswith('#EXTINF'):
        header_done = True
        if current_entry:
            entries.append(current_entry)
        current_entry = [line]
    elif not header_done:
        header.append(line)
    elif line.strip():
        current_entry.append(line)

if current_entry:
    entries.append(current_entry)

# Step 2: Remove duplicates based on the URL (the last line of each entry)
unique_entries = []
seen_urls = set()

for entry in entries:
    # Find the URL (usually the last line of the entry that doesn't start with #)
    url = ""
    for line in reversed(entry):
        trimmed = line.strip()
        if trimmed and not trimmed.startswith('#'):
            url = trimmed
            break
    
    if url:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_entries.append(entry)
    else:
        # If no URL found, keep the entry (it might be a comment or header-like)
        unique_entries.append(entry)

# Step 3: Write back to the file
with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(header)
    for entry in unique_entries:
        f.writelines(entry)

print(f"Removed duplicates. Total entries remaining: {len(unique_entries)}")
