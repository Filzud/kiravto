"""Как ведёт себя getOfferCard при серии запросов: статус и размер каждого ответа."""
import json, random, time, sys
from playwright.sync_api import sync_playwright
from kiravto.source.autoru import FETCH_JS, CARD_EP, LISTING_EP, UNHIDE_FILTERS

pause = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_context(locale="ru-RU").new_page()
    page.goto("https://auto.ru/diler/cars/all/rolf_yasenevo_moskva/", wait_until="domcontentloaded")
    lst = page.evaluate(FETCH_JS, [LISTING_EP, {"category": "cars", "section": "used", "dealer_id": "20866489",
                                                "page": 2, "page_size": 50, "geo_id": [], **UNHIDE_FILTERS}])
    offers = json.loads(lst["text"])["offers"]
    t0 = time.time()
    for i, o in enumerate(offers[:45], 1):
        time.sleep(pause)
        r = page.evaluate(FETCH_JS, [CARD_EP, {"category": "cars", "offer_id": f"{o['id']}-{o['hash']}"}])
        flag = "" if len(r["text"]) > 1000 else " <<< " + r["text"][:80]
        print(f"{i:2d} t={time.time()-t0:5.0f}s status={r['status']} len={len(r['text'])}{flag}", flush=True)
        if not r["text"]:
            for k in range(3):
                time.sleep(10 * (k + 1))
                r = page.evaluate(FETCH_JS, [CARD_EP, {"category": "cars", "offer_id": f"{o['id']}-{o['hash']}"}])
                print(f"   retry{k+1} status={r['status']} len={len(r['text'])}", flush=True)
                if r["text"]:
                    break
    b.close()
