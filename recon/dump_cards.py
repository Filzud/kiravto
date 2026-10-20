import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright
JS = """async ([ep, body]) => {
  const csrf = document.cookie.match(/_csrf_token=([^;]+)/)?.[1];
  const r = await fetch(ep, {method:'POST', headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify(body)});
  return {status: r.status, text: await r.text()};
}"""
async def main():
    out = Path(sys.argv[1])
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await (await b.new_context(locale="ru-RU")).new_page()
        await page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/")
        for oid in sys.argv[2:]:
            r = await page.evaluate(JS, ["/-/ajax/desktop-search/getOfferCard/", {"category": "cars", "offer_id": oid}])
            (out / f"card_{oid}.json").write_text(r["text"], encoding="utf-8")
            print(oid, r["status"], len(r["text"]))
            await asyncio.sleep(2)
        await b.close()
asyncio.run(main())
