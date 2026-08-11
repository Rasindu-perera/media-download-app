import re

with open('downloader.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the incorrectly indented lines completely
content = re.sub(r'\n        ydl_opts\[\'js_runtimes\'\] = \{\'node\': \{\}\}', '', content)

# Now, add it back dynamically with the correct indentation!
def replacer(match):
    indent = match.group(1)
    return f"{indent}ydl_opts['cookiefile'] = \"cookies.txt\"\n{indent}ydl_opts['js_runtimes'] = {{'node': {{}}}}"

content = re.sub(r'([ \t]+)ydl_opts\[\'cookiefile\'\] = "cookies.txt"', replacer, content)

with open('downloader.py', 'w', encoding='utf-8') as f:
    f.write(content)
