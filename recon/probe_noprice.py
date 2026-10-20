"""Ищем объявления без price_info.price в выдаче и карточках (разбор падения прогона #1)."""
import json, sys
from kiravto.config import load_settings
from kiravto.source.autoru import AutoruBrowserSource
s = load_settings()
src = AutoruBrowserSource(s.dealer_code, s.dealer_id)
try:
    offers = []
    for sec in ("new", "used"):
        p = 1
        while True:
            d = src.listing(sec, p)
            offers += d["offers"]
            if p >= d["pagination"]["total_page_count"]: break
            p += 1
    bad = [o for o in offers if "price" not in (o.get("price_info") or {})]
    print("listing", len(offers), "without price", len(bad))
    for o in bad[:3]:
        print(json.dumps({k: o.get(k) for k in ("id","hash","section","status","availability","price_info","url")}, ensure_ascii=False))
    for o in offers[:6]:
        c = src.card(f"{o['id']}-{o['hash']}")
        pi = c.get("price_info")
        print("card", o["id"], o["section"], c.get("status"), "price" in (pi or {}), json.dumps(pi, ensure_ascii=False)[:200])
        if pi is not None and "price" not in pi:
            open(f"recon/dump/card_noprice_{o['id']}.json","w",encoding="utf-8").write(json.dumps(c, ensure_ascii=False))
    print("requests", src.requests)
finally:
    src.close()
