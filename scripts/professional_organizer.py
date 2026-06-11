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
current_group = ''
current_name = ''

header_done = False

for line in lines:
    if line.startswith('#EXTINF'):
        header_done = True
        if current_entry:
            entries.append({
                'name': current_name,
                'old_group': current_group,
                'content': current_entry
            })
        
        current_entry = [line]
        # Extract name
        name_match = re.search(r',([^,\r\n]+)$', line)
        current_name = name_match.group(1).strip() if name_match else 'Unknown'
        
        # Extract group
        group_match = re.search(r'group-title=\"([^\"]+)\"', line)
        current_group = group_match.group(1) if group_match else ''
        
    elif not header_done:
        header.append(line)
    elif line.strip():
        current_entry.append(line)

if current_entry:
    entries.append({
        'name': current_name,
        'old_group': current_group,
        'content': current_entry
    })

def organize_channel(name, old_group):
    name_lower = name.lower()
    group_lower = old_group.lower()
    
    # Kids
    if 'kids' in group_lower or 'kids' in name_lower or 'cartoon' in group_lower or 'cartoon' in name_lower or 'pogo' in name_lower or 'durronto' in name_lower or 'rongeen' in name_lower or 'funny' in name_lower:
        return '01. Kids & Cartoons'
    
    # Sports
    if 'sports' in group_lower or 'sports' in name_lower or 'football' in name_lower or 'cricket' in name_lower or 'ten' in name_lower or 'euro' in name_lower:
        return '07. Sports'
    
    # Religious
    if 'religious' in group_lower or 'islamic' in group_lower or 'quran' in name_lower or 'madani' in name_lower or 'makkah' in name_lower or 'peace tv' in name_lower:
        return '10. Religious'

    # BD News
    if ('bangla' in group_lower or 'bangladesh' in group_lower or '[bd]' in name_lower) and 'news' in name_lower:
        return '02. BD | News'
    if 'news' in group_lower and ('somoy' in name_lower or 'ekattor' in name_lower or 'jamuna' in name_lower or 'dbc' in name_lower or 'atn news' in name_lower or 'independent' in name_lower):
        return '02. BD | News'

    # BD Movies
    if ('bangla' in group_lower or 'bangladesh' in group_lower) and ('movies' in group_lower or 'cinema' in name_lower or 'movies' in name_lower):
        return '04. BD | Movies'

    # BD Entertainment
    if 'bangla' in group_lower or 'bangladesh' in group_lower or '[bd]' in name_lower or 'btv' in name_lower or 'ntv' in name_lower or 'gtv' in name_lower or 'maasranga' in name_lower or 'atn bangla' in name_lower or 'deepto' in name_lower:
        return '03. BD | Entertainment'

    # Hindi Movies
    if 'hindi' in group_lower and ('movies' in group_lower or 'cinema' in name_lower or 'goldmines' in name_lower or 'zee action' in name_lower or 'star gold' in name_lower):
        return '05. Hindi | Movies'
    
    # Hindi Entertainment
    if 'hindi' in group_lower:
        return '06. Hindi | Entertainment'

    # Infotainment
    if 'infotainment' in group_lower or 'discovery' in name_lower or 'nat geo' in name_lower or 'animal' in name_lower or 'travel' in name_lower or 'wild' in name_lower:
        return '08. Infotainment'

    # Music
    if 'music' in group_lower or 'music' in name_lower or 'hits' in name_lower:
        return '09. Music'
        
    # Catch vague ones
    if 'news' in name_lower or 'news' in group_lower:
        return '02. BD | News' if 'bangla' in name_lower or 'bangla' in group_lower else '11. International | News'
    
    if 'movie' in name_lower or 'movie' in group_lower or 'cinema' in name_lower:
        return '04. BD | Movies' if 'bangla' in name_lower else '05. Hindi | Movies'

    return '12. Others'

# Update groups and rebuild entries
final_output = []
for entry in entries:
    new_group = organize_channel(entry['name'], entry['old_group'])
    inf_line = entry['content'][0]
    
    # Replace or add group-title
    if 'group-title="' in inf_line:
        new_inf_line = re.sub(r'group-title=\"[^\"]+\"', f'group-title="{new_group}"', inf_line)
    else:
        new_inf_line = inf_line.replace('#EXTINF:-1', f'#EXTINF:-1 group-title="{new_group}"')
    
    entry['content'][0] = new_inf_line
    entry['new_group'] = new_group

# Sort entries by new_group, then name
entries.sort(key=lambda x: (x['new_group'], x['name'].lower()))

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(header)
    for entry in entries:
        f.writelines(entry['content'])
