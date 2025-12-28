with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace('%s', '?')
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)