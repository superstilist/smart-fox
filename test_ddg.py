import httpx
import re

resp = httpx.post('https://lite.duckduckgo.com/lite/', data={'q': 'anime yowayowa'}, headers={'User-Agent': 'Mozilla/5.0'})
text = resp.text

# Try to find the result snippet and title. Duckduckgo lite uses tables.
# Title is usually in <a class="result-snippet" href="...">...</a>
# Wait, class is result-url for the title link, or result-snippet for the snippet.
results = []
for match in re.finditer(r'<a[^>]+class="result-url"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.DOTALL):
    results.append({"url": match.group(1), "title": match.group(2)})

print("Result URLs found:", len(results))

snippets = []
for match in re.finditer(r'<td class="result-snippet">([^<]*)</td>', text, re.DOTALL):
    snippets.append(match.group(1).strip())

print("Snippets found:", len(snippets))
for i in range(min(2, len(results))):
    print("Title:", results[i]["title"].strip())
    print("URL:", results[i]["url"])
    if i < len(snippets):
        print("Snippet:", snippets[i])
