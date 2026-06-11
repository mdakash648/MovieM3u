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
current_group = 'Unknown'

header_done = False

for line in lines:
    if line.startswith('#EXTINF'):
        header_done = True
        if current_entry:
            entries.append((current_group, ''.join(current_entry)))
        current_entry = [line]
        match = re.search(r'group-title=\"([^\"]+)\"', line)
        if match:
            current_group = match.group(1)
        else:
            current_group = 'Unknown'
    elif not header_done:
        header.append(line)
    elif line.strip():
        current_entry.append(line)

if current_entry:
    entries.append((current_group, ''.join(current_entry)))

# Sort entries by group name
entries.sort(key=lambda x: x[0].lower())

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(header)
    for _, content in entries:
        f.write(content)
