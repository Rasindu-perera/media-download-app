import os
content = open('downloader.py').read()
content = content.replace('ydl_opts[\'cookiefile\'] = "cookies.txt"', 'ydl_opts[\'cookiefile\'] = "cookies.txt"\\n        ydl_opts[\'js_runtimes\'] = {\'node\': {}}')
open('downloader.py', 'w').write(content)
