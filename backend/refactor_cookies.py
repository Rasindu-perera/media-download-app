import os
import re

content = open('downloader.py', 'r', encoding='utf-8').read()

injection = '''
def get_cookie_file():
    # If YOUTUBE_COOKIES env var exists, write it to a temp file
    import os
    if os.environ.get("YOUTUBE_COOKIES"):
        with open("temp_cookies.txt", "w") as f:
            f.write(os.environ.get("YOUTUBE_COOKIES"))
        return "temp_cookies.txt"
    # Otherwise fallback to local file
    if os.path.exists("cookies.txt"):
        return "cookies.txt"
    return None

def apply_cookie_opts(ydl_opts):
    cookie_file = get_cookie_file()
    if cookie_file:
        ydl_opts['cookiefile'] = cookie_file
        ydl_opts['js_runtimes'] = {'node': {}}
'''

content = re.sub(r'(from dataclasses import dataclass)', r'\1\n' + injection, content)

content = re.sub(r'([ \t]+)if os\.path\.exists\("cookies\.txt"\):\n[ \t]+ydl_opts\[\'cookiefile\'\] = "cookies\.txt"\n[ \t]+ydl_opts\[\'js_runtimes\'\] = \{\'node\': \{\}\}', r'\1apply_cookie_opts(ydl_opts)', content)

open('downloader.py', 'w', encoding='utf-8').write(content)
