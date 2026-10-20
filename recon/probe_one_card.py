import sys, json
from playwright.sync_api import sync_playwright
from kiravto.source.autoru import FETCH_JS, CARD_EP
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_context(locale="ru-RU").new_page()
    page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/", wait_until="domcontentloaded")
    for ref in sys.argv[1:]:
        for i in range(2):
            r = page.evaluate(FETCH_JS, [CARD_EP, {"category": "cars", "offer_id": ref}])
            print(ref, i, r["status"], len(r["text"]), r["url"][-60:], r["text"][:150])
        resp = page.goto(f"https://auto.ru/cars/used/sale/{ref}/", wait_until="domcontentloaded")
        print("page", resp.status, page.url[:100], page.title()[:100])
    b.close()
