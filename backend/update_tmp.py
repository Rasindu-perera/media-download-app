import os

target_files = ['main.py', 'downloader.py', 'spotify_handler.py', 'utils.py']

for file in target_files:
    if not os.path.exists(file): continue
    
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if 'from config import GLOBAL_TMP_DIR' not in content:
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith('import ') or line.startswith('from '):
                lines.insert(i, 'from config import GLOBAL_TMP_DIR')
                break
        content = '\n'.join(lines)
    
    content = content.replace('"tmp"', 'GLOBAL_TMP_DIR')
    content = content.replace("'tmp'", 'GLOBAL_TMP_DIR')
    
    with open(file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f'Updated {file}')
