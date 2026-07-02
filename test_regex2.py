import re

with open('test_ddg.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Instead of complex regex, let's just find the result-link and result-snippet blocks.
# result-link always has href and text.
links = re.findall(r'href=[\'"]([^\'"]+)[\'"][^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', text, re.IGNORECASE)
snippets = re.findall(r'<td[^>]*class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', text, re.IGNORECASE | re.DOTALL)

print("Links:", len(links))
print("Snippets:", len(snippets))
for i in range(min(3, len(links))):
    title = re.sub(r'<[^>]+>', '', links[i][1]).strip()
    snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
    print(f"Title: {title}")
    print(f"URL: {links[i][0]}")
    print(f"Snippet: {snippet}")
