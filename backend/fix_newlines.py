import re

with open('downloader.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the literal characters "\n" with an actual newline
content = content.replace('ydl_opts[\'cookiefile\'] = "cookies.txt"\\n        ydl_opts[\'js_runtimes\'] = {\'node\': {}}',
                          'ydl_opts[\'cookiefile\'] = "cookies.txt"\n        ydl_opts[\'js_runtimes\'] = {\'node\': {}}')

with open('downloader.py', 'w', encoding='utf-8') as f:
    f.write(content)
