"""Разовый дамп ответов Авто.ру для разведки и фикстур тестов."""
import asyncio, json, random, sys, time
from pathlib import Path
from playwright.async_api import async_playwright

OUT = Path(sys.argv[1])
JS = """async ([ep, body]) => {
  const csrf = document.cookie.match(/_csrf_token=([^;]+)/)?.[1];
  const r = await fetch(ep, {method:'POST', headers:{'content-type':'application/json','x-csrf-token':csrf}, body: JSON.stringify(body)});
  return {status: r.status, text: await r.text()};
}"""
BASE = {"category": "cars", "dealer_id": "20866489", "page_size": 50, "geo_id": [],
        "damage_group": "ANY", "customs_state_group": "DOESNT_MATTER", "has_image": False}

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await (await b.new_context(locale="ru-RU")).new_page()
        await page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/")
        n = 0
        for section in ("new", "used"):
            pg = 1
            while True:
                r = await page.evaluate(JS, ["/-/ajax/desktop-search/listing/", {**BASE, "section": section, "page": pg}])
                n += 1
                (OUT / f"listing_{section}_p{pg}.json").write_text(r["text"], encoding="utf-8")
                j = json.loads(r["text"])
                print(section, pg, len(j["offers"]), j["pagination"]["total_offers_count"], flush=True)
                if pg >= j["pagination"]["total_page_count"]:
                    break
                pg += 1
                await asyncio.sleep(random.uniform(2, 4))
        for oid in sys.argv[2:]:
            r = await page.evaluate(JS, ["/-/ajax/desktop-search/getOfferCard/", {"category": "cars", "offerID": oid, "geo_id": []}])
            n += 1
            (OUT / f"card_{oid}.json").write_text(r["text"], encoding="utf-8")
            print("card", oid, r["status"], len(r["text"]))
            await asyncio.sleep(2)
        print("requests", n)
        await b.close()

asyncio.run(main())
