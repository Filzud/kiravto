"""Второй источник — склад на сайте самого салона (rolf.ru) и склейка с объявлениями Авто.ру.

Откуда данные: сайт rolf.ru (Nuxt) берёт склад из JSON `apiweb.rolf.ru/api/v2/vehicles/total`
с фильтром `dealer_id[0]=38` («РОЛЬФ Ясенево»). Найдено разведкой 22.10.2026: этот же запрос лежит
в `window.__NUXT__` страницы /cars/used/?dealer_id[0]=38. API отвечает обычному HTTP-клиенту, без входа.

Главное отличие от Авто.ру — сайт салона отдаёт VIN целиком, а Авто.ру только первые 3 символа.
Склейка (одна машина из двух источников — одна запись offer_sources):
* с пробегом — видимая часть VIN + марка + модель + год + пробег (±1 %): пробег до километра почти уникален;
* новые — видимая часть VIN + марка + модель + год + цвет + цена (±3 %), и только если кандидат ровно один:
  машины одной партии неразличимы, лучше не склеить, чем склеить чужую.
"""
from __future__ import annotations

import logging
import random
import re
import sqlite3
import time

import httpx

from . import normalize as nz
from .db import iso, utcnow
from .source.parse import SourceFormatError

log = logging.getLogger("kiravto.rolf")

API = "https://apiweb.rolf.ru/api/v2/vehicles/total"
SITE = "https://www.rolf.ru"
PER_PAGE = 60          # больше сайт не отдаёт
MAX_PAGES = 20
PRICE_TOLERANCE = 0.03
MILEAGE_TOLERANCE = 0.01


def _req(obj: dict, key: str):
    if key not in obj:
        raise SourceFormatError(f"rolf.ru: нет поля '{key}' у машины {obj.get('id')}")
    return obj[key]


def parse_vehicle(it: dict) -> dict:
    vtype = _req(it, "vehicle_type")
    brand, model = _req(it, "brand"), _req(it, "model")
    vins = _req(it, "vin") or []
    price = _req(it, "price")
    disc = it.get("discount_max") or 0
    return {
        "ext_id": str(_req(it, "id")),
        "section": "used" if vtype == "used" else "new",
        "mark_code": nz.resolve_mark(brand.get("name") or brand.get("alias")),
        "model": model.get("name"),
        "year": it.get("year"),
        "mileage": it.get("mileage") or 0,
        "vin": (vins[0] if vins else None),
        "price": int(price) if price else None,
        "price_discount": int(price) - int(disc) if price and disc else (int(price) if price else None),
        # color_name — базовый цвет («Белый»), как в палитре Авто.ру; original_color_name бывает маркетинговым
        "color": (it.get("color_name") or it.get("original_color_name") or "").lower().replace("ё", "е") or None,
        "in_transit": int(bool(it.get("in_transit"))),
        "is_auto_ru": int(bool(it.get("is_auto_ru"))),
        "url": f"{SITE}/cars/{vtype}/{brand.get('alias')}/{model.get('alias')}/{it['id']}/",
    }


class RolfSource:
    def __init__(self, dealer_id: str, pause: tuple[float, float] = (1.0, 2.5), retries: int = 3,
                 client: httpx.Client | None = None):
        self.dealer_id = dealer_id
        self.pause = pause
        self.retries = retries
        self.requests = 0
        self.client = client or httpx.Client(timeout=30, headers={"accept": "application/json",
                                                                  "user-agent": "Mozilla/5.0 (Kiravto stock monitor)"})

    def page(self, vtype: str, page: int) -> dict:
        params = {"page": page, "per_page": PER_PAGE, "city_id": 1, "vehicle_type": vtype, "type": "car",
                  "dealer_id[0]": self.dealer_id}
        delay = 3.0
        for attempt in range(1, self.retries + 1):
            time.sleep(random.uniform(*self.pause))
            self.requests += 1
            try:
                r = self.client.get(API, params=params)
                if r.status_code < 500 and r.status_code != 429:
                    data = r.json()
                    if not data.get("success") or "items" not in (data.get("data") or {}):
                        raise SourceFormatError(f"rolf.ru: неожиданный ответ {str(data)[:150]}")
                    return data["data"]
                log.warning("rolf.ru HTTP %s, попытка %s", r.status_code, attempt)
            except httpx.HTTPError as e:
                log.warning("rolf.ru: %s, попытка %s", e.__class__.__name__, attempt)
            time.sleep(delay)
            delay *= 2
        raise ConnectionError(f"rolf.ru {vtype} стр.{page}: не ответил после {self.retries} попыток")

    def all_vehicles(self) -> list[dict]:
        out = []
        for vtype in ("new", "used"):
            page = 1
            while page <= MAX_PAGES:
                d = self.page(vtype, page)
                out += [parse_vehicle(it) for it in d["items"]]
                last = int((d.get("pagination") or {}).get("last_page") or 1)
                if page >= last or not d["items"]:
                    break
                page += 1
        return out


def _model_key(name: str | None) -> str:
    # «RAV4» / «RAV 4» / «Land Cruiser Prado» → rav4 / landcruiserprado; «5 серии» → 5серии
    return re.sub(r"[^0-9a-zа-я]", "", (name or "").lower())


def vin_compatible(partial: str | None, full: str | None) -> bool:
    """Видимые символы VIN Авто.ру (WAU**************) совпадают с полным VIN сайта."""
    if not partial or not full or len(partial) != len(full):
        return False
    return all(a == "*" or a == b for a, b in zip(partial, full))


def match_candidates(o: dict, stock: list[dict]) -> tuple[list[dict], str]:
    cands = [v for v in stock if v["section"] == o["section"] and v["mark_code"] == o["mark_code"]
             and v["year"] == o["year"] and vin_compatible(o.get("vin"), v["vin"])
             and _model_key(v["model"]) and (_model_key(v["model"]) in _model_key(o["model"])
                                             or _model_key(o["model"]) in _model_key(v["model"]))]
    if o["section"] == "used":
        km = o.get("mileage") or 0
        cands = [v for v in cands if km and abs((v["mileage"] or 0) - km) <= max(100, km * MILEAGE_TOLERANCE)]
        return cands, "VIN (видимая часть) + модель + год + пробег"
    price = o.get("price") or 0
    color = (o.get("color") or "").replace("ё", "е")
    cands = [v for v in cands if price and v["price"] and abs(v["price"] - price) <= price * PRICE_TOLERANCE
             and (not color or not v["color"] or v["color"] == color)]
    return cands, "VIN (видимая часть) + модель + год + цвет + цена"


def match_stock(conn: sqlite3.Connection, stock: list[dict], now_iso: str) -> int:
    """Склеить активные объявления Авто.ру с машинами сайта. Неоднозначное не склеиваем."""
    offers = [dict(r) for r in conn.execute(
        "SELECT id, section, mark_code, model, year, mileage, price, color, vin FROM offers WHERE status='active'")]
    proposals: dict[str, tuple[dict, str]] = {}
    claimed: dict[str, int] = {}
    for o in offers:
        cands, how = match_candidates(o, stock)
        if len(cands) == 1:
            proposals[o["id"]] = (cands[0], how)
            claimed[cands[0]["ext_id"]] = claimed.get(cands[0]["ext_id"], 0) + 1
    n = 0
    for oid, (v, how) in proposals.items():
        if claimed[v["ext_id"]] > 1:
            continue  # на одну машину сайта претендуют два объявления — это партия, не склеиваем
        conn.execute(
            "INSERT INTO offer_sources(offer_id, source, ext_id, url, vin, price, price_discount, matched_by, "
            "first_seen, last_seen) VALUES(?, 'rolf', ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(offer_id, source) DO UPDATE SET ext_id=excluded.ext_id, url=excluded.url, vin=excluded.vin, "
            "price=excluded.price, price_discount=excluded.price_discount, matched_by=excluded.matched_by, "
            "last_seen=excluded.last_seen",
            (oid, v["ext_id"], v["url"], v["vin"], v["price"], v["price_discount"], how, now_iso, now_iso))
        n += 1
    conn.commit()
    return n


def sync_rolf(conn: sqlite3.Connection, settings, source: RolfSource | None = None) -> dict:
    now = iso(utcnow())
    src = source or RolfSource(settings.rolf_dealer_id)
    run_id = conn.execute("INSERT INTO side_runs(kind, started_at) VALUES('rolf', ?)", (now,)).lastrowid
    conn.commit()
    status, note, items, matched = "complete", None, 0, 0
    try:
        stock = src.all_vehicles()
        items = len(stock)
        conn.execute("DELETE FROM rolf_stock")
        conn.executemany(
            "INSERT OR REPLACE INTO rolf_stock(ext_id, section, mark_code, model, year, mileage, vin, price, "
            "price_discount, color, in_transit, is_auto_ru, url, seen_at) "
            "VALUES(:ext_id, :section, :mark_code, :model, :year, :mileage, :vin, :price, :price_discount, :color, "
            ":in_transit, :is_auto_ru, :url, :seen_at)", [v | {"seen_at": now} for v in stock])
        matched = match_stock(conn, stock, now)
        active = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]
        note = f"на сайте салона {items}, на Авто.ру в продаже {active}, склеено {matched}"
    except SourceFormatError as e:
        status, note = "failed", f"СМЕНА ФОРМАТА: {e}"
    except ConnectionError as e:
        status, note = "incomplete", str(e)
    conn.execute("UPDATE side_runs SET finished_at=?, status=?, requests=?, items=?, matched=?, note=? WHERE id=?",
                 (iso(utcnow()), status, src.requests, items, matched, note, run_id))
    conn.commit()
    log.info("rolf.ru: %s, %s", status, note)
    return {"id": run_id, "status": status, "requests": src.requests, "items": items, "matched": matched, "note": note}
