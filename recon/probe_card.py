import asyncio, json, sys
from playwright.async_api import async_playwright
JS = """async ([ep, body]) => {
  const csrf = document.cookie.match(/_csrf_token=([^;]+)/)?.[1];
  const r = await fetch(ep, {method:'POST', redirect:'manual', headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify(body)});
  return {status: r.status, type: r.type, url: r.url, text: await r.text()};
}"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await (await b.new_context(locale="ru-RU")).new_page()
        await page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/")
        for body in ({"category":"cars","offer_id":"1160027054-72d39620"},
                     {"category":"cars","offerID":"1160027054-72d39620","offer_id":"1160027054-72d39620"},
                     {"category":"cars","offer_id":"1160027054"}):
            for ep in ("/-/ajax/desktop-search/getOfferCard/", "/-/ajax/desktop/getOfferCard/"):
                r = await page.evaluate(JS, [ep, body])
                print(ep, body, r["status"], r["type"], r["text"][:120])
        r = await page.request.get("https://avatars.avto.ru/get-autoru-vos/21151957/65fdb6d7d304b8c71ff6d6796f4c26fe/orig")
        print("orig", r.status, r.headers.get("content-type"), len(await r.body()))
        await b.close()
asyncio.run(main())
