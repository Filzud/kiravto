"""«Ниже рынка»: насколько цена салона ниже или выше медианы таких же машин у других продавцов Авто.ру.

Похожие — та же марка, модель и поколение, год ±1, пробег ±20 % (у новых пробег не сравниваем),
Москва и 200 км вокруг. Машины самого салона в выборку не входят.

Бережно к источнику: один поиск на группу (марка, модель, поколение, раздел) с диапазоном годов и пробегов
сразу для всех машин группы, дальше фильтруем у себя. За прогон — не больше MARKET_GROUPS_PER_RUN групп,
выборка группы живёт MARKET_TTL_HOURS. Весь склад (~160 групп) обходится примерно за сутки.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

from .db import iso, parse_iso, utcnow
from .source.parse import SourceBlocked, SourceFormatError, _req

log = logging.getLogger("kiravto.market")

YEAR_SPREAD = 1
MILEAGE_SPREAD = 0.20
MAX_PAGES = 2          # до 100 похожих на группу — для медианы достаточно


def group_key(o: dict) -> str:
    return f"{o['section']}:{o['mark_code']}:{o['model_code']}:{o['generation_id']}"


def plan_groups(conn: sqlite3.Connection, now: datetime, ttl_hours: float) -> list[dict]:
    """Группы активных машин, для которых выборки нет, она устарела или не покрывает годы/пробеги."""
    rows = [dict(r) for r in conn.execute(
        "SELECT section, mark_code, model_code, generation_id, year, mileage FROM offers "
        "WHERE status='active' AND generation_id IS NOT NULL AND year IS NOT NULL")]
    groups: dict[str, dict] = {}
    for o in rows:
        g = groups.setdefault(group_key(o), {"key": group_key(o), "section": o["section"], "mark_code": o["mark_code"],
                                             "model_code": o["model_code"], "generation_id": o["generation_id"],
                                             "years": [], "kms": []})
        g["years"].append(o["year"])
        if o["section"] == "used" and o["mileage"]:
            g["kms"].append(o["mileage"])
    cached = {r["group_key"]: dict(r) for r in conn.execute("SELECT * FROM market_comps")}
    todo = []
    for g in groups.values():
        g["year_from"], g["year_to"] = min(g["years"]) - YEAR_SPREAD, max(g["years"]) + YEAR_SPREAD
        if g["kms"]:
            g["km_from"] = int(min(g["kms"]) * (1 - MILEAGE_SPREAD))
            g["km_to"] = int(max(g["kms"]) * (1 + MILEAGE_SPREAD)) + 1
        else:
            g["km_from"] = g["km_to"] = None
        c = cached.get(g["key"])
        if c:
            fresh = parse_iso(c["fetched_at"]) > now - timedelta(hours=ttl_hours)
            covers = c["year_from"] <= g["year_from"] and c["year_to"] >= g["year_to"] and \
                (g["km_from"] is None or (c["km_from"] is not None and c["km_from"] <= g["km_from"]
                                          and c["km_to"] >= g["km_to"]))
            if fresh and covers:
                continue
        g["fetched_at"] = c["fetched_at"] if c else ""
        todo.append(g)
    todo.sort(key=lambda g: g["fetched_at"])  # сначала никогда не искавшиеся, потом самые старые
    return todo


def search_filter(g: dict) -> dict:
    flt = {"section": g["section"], "year_from": g["year_from"], "year_to": g["year_to"],
           "catalog_filter": [{"mark": g["mark_code"], "model": g["model_code"], "generation": g["generation_id"]}]}
    if g["km_from"] is not None:
        flt |= {"km_age_from": g["km_from"], "km_age_to": g["km_to"]}
    return flt


def parse_comps(data: dict, own_dealer: str) -> list[list]:
    """Выдача поиска → [[id, год, пробег, цена, салон]]; машины нашего салона выкидываем."""
    out = []
    for raw in data["offers"]:
        salon = (raw.get("salon") or {}).get("code")
        if salon == own_dealer:
            continue
        if (raw.get("groupping_info") or {}).get("size", 1) > 1:
            # карточка-группа «N предложений» с одной ценой — не машина, в медиану не берём
            log.warning("рынок: в выдаче группа из %s машин — запрошено не списком?", raw["groupping_info"]["size"])
            continue
        out.append([str(_req(raw, "id")), _req(raw, "documents.year"), _req(raw, "state").get("mileage") or 0,
                    int(_req(raw, "price_info.price")), salon])
    return out


def similar(o: dict, items: list[list]) -> list[int]:
    """Цены похожих на машину o: год ±1, у машин с пробегом — пробег ±20 %."""
    prices = []
    for _id, year, km, price, _salon in items:
        if not year or abs(year - o["year"]) > YEAR_SPREAD or price <= 0:
            continue
        if o["section"] == "used" and o.get("mileage"):
            if abs(km - o["mileage"]) > o["mileage"] * MILEAGE_SPREAD:
                continue
        prices.append(price)
    return prices


def median(xs: list[int]) -> float:
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def recompute(conn: sqlite3.Connection, min_comps: int, now: datetime | None = None) -> int:
    """Пересчитать положение каждой активной машины относительно рынка (локально, без запросов)."""
    now = now or utcnow()
    comps = {r["group_key"]: json.loads(r["items"]) for r in conn.execute("SELECT group_key, items FROM market_comps")}
    n = 0
    for r in conn.execute("SELECT id, section, mark_code, model_code, generation_id, year, mileage, price "
                          "FROM offers WHERE status='active' AND generation_id IS NOT NULL").fetchall():
        o = dict(r)
        items = comps.get(group_key(o))
        if items is None:
            continue
        prices = similar(o, items) if o["year"] and o["price"] else []
        if len(prices) < min_comps:
            # мало похожих — честно «не знаем», а не процент по двум машинам
            conn.execute("UPDATE offers SET market_median=NULL, market_n=?, market_pct=NULL, market_at=? WHERE id=?",
                         (len(prices), iso(now), o["id"]))
            continue
        med = median(prices)
        pct = round((o["price"] - med) / med * 100, 1)
        conn.execute("UPDATE offers SET market_median=?, market_n=?, market_pct=?, market_at=? WHERE id=?",
                     (int(med), len(prices), pct, iso(now), o["id"]))
        n += 1
    conn.commit()
    return n


class MarketScanner:
    def __init__(self, conn: sqlite3.Connection, source, settings, clock: Callable[[], datetime] = utcnow):
        self.conn = conn
        self.source = source
        self.s = settings
        self.clock = clock

    def run(self, max_groups: int | None = None) -> dict:
        now = self.clock()
        limit = self.s.market_groups_per_run if max_groups is None else max_groups
        todo = plan_groups(self.conn, now, self.s.market_ttl_hours)[:limit]
        req0 = getattr(self.source, "requests", 0)
        cur = self.conn.execute("INSERT INTO side_runs(kind, started_at) VALUES('market', ?)", (iso(now),))
        run_id = cur.lastrowid
        self.conn.commit()
        done, status, note = 0, "complete", None
        for g in todo:
            try:
                items, total = self._fetch(g)
            except SourceBlocked as e:
                status, note = "incomplete", f"блокировка: {e}; остальное — в следующий прогон"
                break
            except SourceFormatError as e:
                status, note = "failed", f"СМЕНА ФОРМАТА: {e}"
                break
            self.conn.execute(
                "INSERT INTO market_comps(group_key, section, mark_code, model_code, generation_id, year_from, year_to, "
                "km_from, km_to, total, items, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(group_key) DO UPDATE SET year_from=excluded.year_from, year_to=excluded.year_to, "
                "km_from=excluded.km_from, km_to=excluded.km_to, total=excluded.total, items=excluded.items, "
                "fetched_at=excluded.fetched_at",
                (g["key"], g["section"], g["mark_code"], g["model_code"], g["generation_id"], g["year_from"],
                 g["year_to"], g["km_from"], g["km_to"], total, json.dumps(items), iso(self.clock())))
            self.conn.commit()
            done += 1
        priced = recompute(self.conn, self.s.market_min_comps, self.clock())
        left = len(plan_groups(self.conn, self.clock(), self.s.market_ttl_hours))
        requests = getattr(self.source, "requests", 0) - req0
        note = "; ".join(filter(None, [note, f"групп обновлено {done}, ждут {left}"]))
        self.conn.execute("UPDATE side_runs SET finished_at=?, status=?, requests=?, items=?, matched=?, note=? "
                          "WHERE id=?", (iso(self.clock()), status, requests, done, priced, note, run_id))
        self.conn.commit()
        log.info("рынок: %s, групп %s, запросов %s, машин с оценкой %s", status, done, requests, priced)
        return {"id": run_id, "status": status, "groups": done, "groups_left": left, "requests": requests,
                "priced": priced, "note": note}

    def _fetch(self, g: dict) -> tuple[list[list], int]:
        flt = search_filter(g)
        items: dict[str, list] = {}
        total = 0
        for page in range(1, MAX_PAGES + 1):
            data = self.source.search(flt, page)
            pag = data["pagination"]
            total = int(pag["total_offers_count"])
            # выдача сдвигается между страницами — одна машина может прийти дважды, в медиану берём один раз
            for it in parse_comps(data, self.s.dealer_code):
                items.setdefault(it[0], it)
            if page >= int(pag["total_page_count"]) or not data["offers"]:
                break
        return list(items.values()), total


def market_line(o: dict) -> str | None:
    """Строка для поста: «ниже рынка на 7 %» с медианой и числом похожих. None — рынок не посчитан."""
    pct, med, n = o.get("market_pct"), o.get("market_median"), o.get("market_n")
    if pct is None or not med:
        return None
    rub = f"{int(med):,}".replace(",", " ") + " ₽"
    if pct <= -0.5:
        head = f"ниже рынка на {abs(pct):.0f}%"
    elif pct >= 0.5:
        head = f"выше рынка на {pct:.0f}%"
    else:
        head = "по рынку"
    return f"📊 {head} (медиана {rub} по {n} похожим на Авто.ру)"


def passes_filter(o: dict, below_pct: float | None) -> bool:
    """В сообщество — только то, что ниже рынка на N %. Фильтр выключен (None) — проходят все."""
    if below_pct is None:
        return True
    return o.get("market_pct") is not None and o["market_pct"] <= -below_pct
