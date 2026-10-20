"""Цена в выдаче против цены в карточке для одних и тех же объявлений (разбор сверки 21.10)."""
import json, sys, time
from kiravto.config import load_settings
from kiravto.source.autoru import AutoruBrowserSource
ids = set(sys.argv[1:])
s = load_settings()
src = AutoruBrowserSource(s.dealer_code, s.dealer_id)
try:
    found = {}
    for sec in ("new", "used"):
        p = 1
        while True:
            d = src.listing(sec, p)
            for o in d["offers"]:
                if o["id"] in ids:
                    found[o["id"]] = o
            if p >= d["pagination"]["total_page_count"]: break
            p += 1
    for oid, o in found.items():
        c = src.card(f"{oid}-{o['hash']}")
        print(oid, "выдача:", o["price_info"]["price"], (o.get("discount_options") or {}).get("max_discount"),
              "| карточка:", c.get("price_info", {}).get("price"), (c.get("discount_options") or {}).get("max_discount"),
              "| update_date выдача/карточка:", o["additional_info"].get("update_date"), c.get("additional_info", {}).get("update_date"))
    print("не найдено в выдаче:", ids - set(found), "запросов", src.requests)
finally:
    src.close()
