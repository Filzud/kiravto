"""Чтение данных для API, MCP, бота и отчёта. Одна реализация — одинаковые ответы везде."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import normalize as nz
from .db import iso, row_to_offer, utcnow

SORTS = {"price": "price ASC", "-price": "price DESC", "year": "year ASC", "-year": "year DESC",
         "new": "first_seen DESC", "mileage": "mileage ASC"}


def search_offers(conn: sqlite3.Connection, *, mark: str | None = None, model: str | None = None,
                  section: str | None = None, price_min: int | None = None, price_max: int | None = None,
                  year_min: int | None = None, year_max: int | None = None, availability: str | None = None,
                  status: str | None = "active", sort: str = "price", limit: int = 50, offset: int = 0) -> dict:
    where, args = [], []
    if status and status != "all":
        where.append("status=?"); args.append(status)
    if mark:
        where.append("mark_code=?"); args.append(nz.resolve_mark(mark))
    if model:
        where.append("(model_code=? OR UPPER(model)=UPPER(?))"); args += [nz.resolve_model(model), model]
    if section:
        where.append("section=?"); args.append(section)
    if price_min is not None:
        where.append("price>=?"); args.append(price_min)
    if price_max is not None:
        where.append("price<=?"); args.append(price_max)
    if year_min is not None:
        where.append("year>=?"); args.append(year_min)
    if year_max is not None:
        where.append("year<=?"); args.append(year_max)
    if availability:
        av = {v: k for k, v in nz.AVAILABILITY.items()}.get(availability.lower(), availability.upper())
        where.append("availability=?"); args.append(av)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM offers {sql_where}", args).fetchone()[0]
    order = SORTS.get(sort, "price ASC")
    rows = conn.execute(f"SELECT * FROM offers {sql_where} ORDER BY {order} LIMIT ? OFFSET ?",
                        [*args, max(1, min(limit, 200)), max(0, offset)]).fetchall()
    return {"total": total, "items": [brief(row_to_offer(r)) for r in rows]}


def brief(o: dict) -> dict:
    return {k: o.get(k) for k in ("id", "url", "section", "mark", "model", "generation", "complectation", "year",
                                  "mileage", "price", "price_discount", "availability", "color", "status",
                                  "first_seen", "anomalies", "market_pct", "market_median", "market_n")}


def get_offer(conn: sqlite3.Connection, offer_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM offers WHERE id=?", (str(offer_id),)).fetchone()
    if not row:
        return None
    o = row_to_offer(row)
    facts = conn.execute("SELECT facts FROM llm_cache WHERE desc_sha=?", (desc_key(o.get("description")),)).fetchone() \
        if o.get("description") else None
    o["facts"] = json.loads(facts["facts"]) if facts else []
    o["sources"] = offer_sources(conn, o["id"])
    return o


def offer_sources(conn: sqlite3.Connection, offer_id: str) -> list[dict]:
    """Та же машина во втором источнике (сайт салона): полный VIN, своя цена, по какому признаку склеено."""
    return [dict(r) for r in conn.execute(
        "SELECT source, ext_id, url, vin, price, price_discount, matched_by, first_seen, last_seen "
        "FROM offer_sources WHERE offer_id=?", (str(offer_id),))]


def below_market(conn: sqlite3.Connection, min_pct: float = 5.0, section: str | None = None,
                 limit: int = 50) -> dict:
    """Машины дешевле медианы похожих у других продавцов Авто.ру минимум на min_pct процентов."""
    where, args = ["status='active'", "market_pct IS NOT NULL", "market_pct <= ?"], [-abs(min_pct)]
    if section:
        where.append("section=?")
        args.append(section)
    rows = conn.execute(f"SELECT * FROM offers WHERE {' AND '.join(where)} ORDER BY market_pct LIMIT ?",
                        [*args, max(1, min(limit, 200))]).fetchall()
    return {"min_pct": min_pct, "items": [brief(row_to_offer(r)) for r in rows], "coverage": market_coverage(conn)}


def market_coverage(conn: sqlite3.Connection) -> dict:
    c = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {"active": c("SELECT COUNT(*) FROM offers WHERE status='active'"),
            "with_estimate": c("SELECT COUNT(*) FROM offers WHERE status='active' AND market_pct IS NOT NULL"),
            "too_few_comps": c("SELECT COUNT(*) FROM offers WHERE status='active' AND market_at IS NOT NULL "
                               "AND market_pct IS NULL"),
            "below_market": c("SELECT COUNT(*) FROM offers WHERE status='active' AND market_pct <= -0.5"),
            "above_market": c("SELECT COUNT(*) FROM offers WHERE status='active' AND market_pct >= 0.5"),
            "groups_cached": c("SELECT COUNT(*) FROM market_comps"),
            "last_scan": c("SELECT MAX(fetched_at) FROM market_comps")}


def second_source_summary(conn: sqlite3.Connection) -> dict:
    c = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    last = conn.execute("SELECT * FROM side_runs WHERE kind='rolf' ORDER BY id DESC LIMIT 1").fetchone()
    return {"site_total": c("SELECT COUNT(*) FROM rolf_stock"),
            "site_not_on_autoru": c("SELECT COUNT(*) FROM rolf_stock WHERE is_auto_ru=0"),
            "matched": c("SELECT COUNT(*) FROM offer_sources s JOIN offers o ON o.id=s.offer_id "
                         "WHERE o.status='active'"),
            "price_differs": c("SELECT COUNT(*) FROM offer_sources s JOIN offers o ON o.id=s.offer_id "
                               "WHERE o.status='active' AND s.price IS NOT NULL AND s.price != o.price"),
            "last_sync": dict(last) if last else None}


def side_runs(conn: sqlite3.Connection, kind: str | None = None, limit: int = 10) -> list[dict]:
    sql = "SELECT * FROM side_runs" + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, ((kind, limit) if kind else (limit,)))]


def desc_key(text: str | None) -> str:
    import hashlib
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def get_history(conn: sqlite3.Connection, offer_id: str) -> dict | None:
    if not conn.execute("SELECT 1 FROM offers WHERE id=?", (str(offer_id),)).fetchone():
        return None
    prices = [dict(r) for r in conn.execute(
        "SELECT ts, price, price_discount, run_id FROM price_history WHERE offer_id=? ORDER BY ts", (str(offer_id),))]
    events = [dict(r) for r in conn.execute(
        "SELECT ts, type, old, new, details, run_id FROM events WHERE offer_id=? ORDER BY ts, id", (str(offer_id),))]
    return {"offer_id": str(offer_id), "prices": prices, "events": events}


def price_drops(conn: sqlite3.Connection, days: int = 7, min_pct: float = 5.0, now: datetime | None = None) -> list[dict]:
    """Снижения цены за период: первая цена в окне против текущей. В ответе — откуда цифры (события и прогоны)."""
    now = now or utcnow()
    since = iso(now - timedelta(days=days))
    out = []
    rows = conn.execute(
        "SELECT offer_id, MIN(ts) AS first_ts FROM events WHERE type='price_changed' AND ts>=? GROUP BY offer_id",
        (since,)).fetchall()
    for r in rows:
        evs = conn.execute("SELECT ts, old, new, run_id FROM events WHERE offer_id=? AND type='price_changed' "
                           "AND ts>=? ORDER BY ts, id", (r["offer_id"], since)).fetchall()
        old_price = int(evs[0]["old"])
        new_price = int(evs[-1]["new"])
        if old_price <= 0 or new_price >= old_price:
            continue
        pct = (old_price - new_price) / old_price * 100
        if pct < min_pct:
            continue
        o = conn.execute("SELECT * FROM offers WHERE id=?", (r["offer_id"],)).fetchone()
        if not o:
            continue
        out.append({**brief(row_to_offer(o)), "old_price": old_price, "new_price": new_price,
                    "drop_pct": round(pct, 2), "drop_rub": old_price - new_price,
                    "changes": [dict(e) for e in evs]})
    out.sort(key=lambda d: -d["drop_pct"])
    return out


def stock_summary(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or utcnow()
    c = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    by_section = {r[0]: r[1] for r in conn.execute(
        "SELECT section, COUNT(*) FROM offers WHERE status='active' GROUP BY section")}
    by_avail = {nz.AVAILABILITY.get(r[0], r[0] or "не указано"): r[1] for r in conn.execute(
        "SELECT availability, COUNT(*) FROM offers WHERE status='active' GROUP BY availability")}
    top_marks = [{"mark": r[0], "count": r[1]} for r in conn.execute(
        "SELECT mark, COUNT(*) n FROM offers WHERE status='active' GROUP BY mark ORDER BY n DESC LIMIT 10")]
    week_ago = iso(now - timedelta(days=7))
    return {
        "active": c("SELECT COUNT(*) FROM offers WHERE status='active'"),
        "by_section": by_section,
        "by_availability": by_avail,
        "removed": c("SELECT COUNT(*) FROM offers WHERE status='removed'"),
        "probably_sold": c("SELECT COUNT(*) FROM offers WHERE status='sold'"),
        "new_7d": c("SELECT COUNT(*) FROM offers WHERE first_seen>=?", week_ago),
        "price_changes_7d": c("SELECT COUNT(*) FROM events WHERE type='price_changed' AND ts>=?", week_ago),
        "with_anomalies": c("SELECT COUNT(*) FROM offers WHERE status='active' AND anomalies IS NOT NULL"),
        "duplicates": c("SELECT COUNT(*) FROM offers WHERE status='active' AND duplicate_of IS NOT NULL"),
        "price_min": c("SELECT MIN(price) FROM offers WHERE status='active'"),
        "price_median": _median([r[0] for r in conn.execute("SELECT price FROM offers WHERE status='active'")]),
        "price_max": c("SELECT MAX(price) FROM offers WHERE status='active'"),
        "top_marks": top_marks,
        "last_run": next(iter(last_runs(conn, 1)), None),
        "llm": llm_stats(conn),
        "market": market_coverage(conn),
        "second_source": second_source_summary(conn),
    }


def _median(xs: list[int]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def last_runs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def last_success(conn: sqlite3.Connection) -> dict | None:
    r = conn.execute("SELECT * FROM runs WHERE status='complete' ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def llm_stats(conn: sqlite3.Connection) -> dict:
    """Статистика функции фактов: вызовы, токены, деньги, сколько фактов оставлено и выброшено проверкой цитат."""
    t = conn.execute("SELECT COALESCE(SUM(calls),0), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0), "
                     "COALESCE(SUM(cost),0), COALESCE(SUM(facts_kept),0), COALESCE(SUM(facts_dropped),0) "
                     "FROM llm_usage").fetchone()
    cached = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    return {"calls": t[0], "tokens_in": t[1], "tokens_out": t[2], "cost_usd": round(t[3], 6),
            "facts_kept": t[4], "facts_dropped": t[5], "descriptions_cached": cached}


def post_queue(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, kind, status, source, created_at, attempts, last_error FROM posts "
        "WHERE status IN ('new','moderation','approved','sending','failed') ORDER BY id")]
