import httpx

resp = httpx.post('https://lite.duckduckgo.com/lite/', data={'q': 'anime yowayowa'}, headers={'User-Agent': 'Mozilla/5.0'})
with open('test_ddg.html', 'w', encoding='utf-8') as f:
    f.write(resp.text)
