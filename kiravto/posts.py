"""Посты: тексты для людей и очередь модерации.

Здесь посты только СОЗДАЮТСЯ (статус 'new'). Отправить в канал может лишь бот —
и только пост в статусе 'approved', который человек одобрил кнопкой в админ-чате.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import normalize as nz
from .db import iso, parse_iso, utcnow
from .market import market_line, passes_filter

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
ALBUM_LIMIT = 10


def fmt_rub(v: int | None) -> str:
    if v is None:
        return "цена не указана"
    return f"{v:,}".replace(",", " ") + " ₽"


def fmt_km(v: int | None) -> str:
    return "новый" if not v else f"{v:,}".replace(",", " ") + " км"


def title(o: dict) -> str:
    parts = [o.get("mark"), o.get("model"), o.get("generation") if o.get("generation") not in (None, "", "I") else None]
    return " ".join(p for p in parts if p)


def short_card(o: dict) -> str:
    """Одна строка-карточка: модель, год, пробег, цена, ссылка. Без None и сырого JSON."""
    price = fmt_rub(o.get("price"))
    if o.get("price_discount") and o["price_discount"] < (o.get("price") or 0):
        price += f" (со скидками {fmt_rub(o['price_discount'])})"
    avail = nz.AVAILABILITY.get(o.get("availability") or "", "")
    bits = [f"<b>{_esc(title(o))}</b>, {o.get('year') or 'год не указан'}", fmt_km(o.get("mileage")), price]
    if avail and avail != "в наличии":
        bits.append(avail)
    market = market_line(o)
    return " · ".join(bits) + (f"\n{market}" if market else "") + f"\n{o['url']}"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return cut[: cut.rfind("\n")] if "\n" in cut else cut + "…"


def drop_post(o: dict, old_price: int, new_price: int) -> dict:
    pct = (old_price - new_price) / old_price * 100
    year = f", {o['year']}" if o.get("year") else ""
    market = market_line(o)
    text = (f"📉 <b>Цена снижена</b>\n{_esc(title(o))}{year}, {fmt_km(o.get('mileage'))}\n"
            f"Было {fmt_rub(old_price)} → стало {fmt_rub(new_price)} (−{pct:.1f}%)\n"
            + (market + "\n" if market else "") + o["url"])
    photos = (o.get("photos") or [])[:1]
    return {"text": _trim(text, CAPTION_LIMIT if photos else TEXT_LIMIT), "photos": photos}


def daily_new_post(offers: list[dict], day_label: str) -> dict:
    head = f"🆕 <b>Новые за день</b> ({day_label}) — {len(offers)} шт."
    cards = [short_card(o) for o in offers]
    photos = [o["photos"][0] for o in offers if o.get("photos")][:ALBUM_LIMIT]
    # подпись к альбому короткая, полный список — отдельным сообщением
    caption = _trim(head + "\n\n" + "\n\n".join(cards[:3]) + ("\n\n…список ниже" if len(cards) > 3 else ""),
                    CAPTION_LIMIT)
    text = _trim(head + "\n\n" + "\n\n".join(cards), TEXT_LIMIT)
    return {"caption": caption, "text": text, "photos": photos}


def queue_post(conn: sqlite3.Connection, kind: str, dedup_key: str, payload: dict, offer_ids: list[str],
               source: str = "service", now: datetime | None = None) -> int | None:
    """Ставит пост в очередь модерации. Повтор с тем же ключом игнорируется (None)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO posts(kind, dedup_key, offer_ids, payload, source, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, 'new', ?)",
        (kind, dedup_key, json.dumps(offer_ids), json.dumps(payload, ensure_ascii=False), source,
         iso(now or utcnow())))
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def recent_drop_posted(conn: sqlite3.Connection, offer_id: str, now: datetime) -> bool:
    """Была ли эта машина в «Цена снижена» за последние сутки (в любом статусе, кроме отклонённого)."""
    since = iso(now - timedelta(hours=24))
    row = conn.execute(
        "SELECT 1 FROM posts WHERE kind='price_drop' AND created_at >= ? AND status != 'rejected' "
        "AND offer_ids = ?", (since, json.dumps([offer_id]))).fetchone()
    return row is not None


def create_drop_posts(conn: sqlite3.Connection, min_pct: float, now: datetime | None = None,
                      tz: str = "Europe/Moscow", market_below_pct: float | None = None) -> list[int]:
    """Снижения цены ≥ min_pct за последний прогон → посты в модерацию (не чаще раза в сутки на машину)."""
    now = now or utcnow()
    day = now.astimezone(ZoneInfo(tz)).date().isoformat()
    since = iso(now - timedelta(hours=24))
    rows = conn.execute(
        "SELECT id, offer_id, old, new, ts FROM events WHERE type='price_changed' AND ts >= ? ORDER BY ts", (since,)
    ).fetchall()
    created = []
    for ev in rows:
        old, new = int(ev["old"]), int(ev["new"])
        if new >= old or (old - new) / old * 100 < min_pct:
            continue
        o = conn.execute("SELECT * FROM offers WHERE id=? AND status='active'", (ev["offer_id"],)).fetchone()
        if not o or recent_drop_posted(conn, ev["offer_id"], now):
            continue
        offer = dict(o)
        offer["photos"] = json.loads(offer["photos"] or "[]")
        if not passes_filter(offer, market_below_pct):
            continue
        # ключ — само событие снижения: после «Отклонить» тот же пост не появится и в другой день
        if conn.execute("SELECT 1 FROM posts WHERE dedup_key LIKE ?", (f"drop:{ev['offer_id']}:ev{ev['id']}%",)).fetchone():
            continue
        pid = queue_post(conn, "price_drop", f"drop:{ev['offer_id']}:ev{ev['id']}:{day}", drop_post(offer, old, new),
                         [ev["offer_id"]], now=now)
        if pid:
            created.append(pid)
    return created


def create_daily_post(conn: sqlite3.Connection, now: datetime | None = None, tz: str = "Europe/Moscow",
                      limit: int = 30, market_below_pct: float | None = None) -> int | None:
    """«Новые за день»: машины, впервые увиденные за прошедшие сутки. Один пост в календарный день."""
    now = now or utcnow()
    local_day = now.astimezone(ZoneInfo(tz)).date()
    # окно — с момента прошлого дневного поста: иначе машины между постами выпадали бы или дублировались
    prev = conn.execute("SELECT MAX(created_at) FROM posts WHERE kind='daily_new'").fetchone()[0]
    since = prev or iso(now - timedelta(hours=24))
    # первый полный прогон видит «новым» весь склад — такие машины в пост не берём
    baseline = conn.execute("SELECT MIN(finished_at) FROM runs WHERE status='complete'").fetchone()[0]
    if baseline and baseline > since:
        since = baseline
    rows = conn.execute(
        "SELECT * FROM offers WHERE status='active' AND first_seen > ? AND relisted_from IS NULL "
        "AND duplicate_of IS NULL ORDER BY price_discount ASC LIMIT ?", (since, limit)).fetchall()
    if not rows:
        return None
    offers = []
    for r in rows:
        d = dict(r)
        d["photos"] = json.loads(d["photos"] or "[]")
        if passes_filter(d, market_below_pct):
            offers.append(d)
    if not offers:
        return None
    payload = daily_new_post(offers, local_day.strftime("%d.%m.%Y"))
    return queue_post(conn, "daily_new", f"daily_new:{local_day.isoformat()}", payload, [o["id"] for o in offers],
                      now=now)


def due_daily(now: datetime, post_time: str, tz: str) -> bool:
    local = now.astimezone(ZoneInfo(tz))
    hh, mm = (int(x) for x in post_time.split(":"))
    return (local.hour, local.minute) >= (hh, mm)


def parse_payload(row: sqlite3.Row) -> dict:
    return json.loads(row["payload"])


__all__ = ["queue_post", "create_drop_posts", "create_daily_post", "drop_post", "daily_new_post", "short_card",
           "fmt_rub", "parse_iso"]
