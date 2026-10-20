import asyncio, json, sys
from playwright.async_api import async_playwright

JS = """async (body) => {
  const csrf = document.cookie.match(/_csrf_token=([^;]+)/)?.[1];
  const r = await fetch('/-/ajax/desktop-search/listing/', {method:'POST',
    headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify(body)});
  return {status: r.status, text: await r.text()};
}"""

async def main(headless):
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(locale="ru-RU", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
        page = await ctx.new_page()
        resp = await page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/")
        print("goto", resp.status, page.url)
        res = await page.evaluate(JS, {"category":"cars","section":"all","dealer_id":"20866489","page":1,"page_size":50,"geo_id":[]})
        print(res["status"], res["text"][:200])
        await b.close()

asyncio.run(main(sys.argv[1] == "1"))
