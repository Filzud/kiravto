"""Один прогон сборщика: выдача → карточки новых/изменившихся → история → снятия.

Правила (подробно в README):
* «пропало из выдачи» ещё не «снято»: снятие только в полном прогоне и после проверки карточки;
* прогон неполный, если источник заблокировал нас или собрано < 90% прошлого склада —
  тогда никого не снимаем;
* «перевыставлено» — новый ID той же машины (VIN → хеш фото → набор характеристик);
* «вероятно продано» — снято и не вернулось за N дней.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Callable

from . import normalize as nz
from .config import Settings
from .db import iso, kv_get, kv_set, parse_iso, utcnow
from .photohash import dhash, hamming
from .source.autoru import Source
from .source.parse import (SourceBlocked, SourceFormatError, check_listing_response, listing_signature,
                           parse_card, parse_offer)

log = logging.getLogger("kiravto.collector")

SECTIONS = ("new", "used")
ALT_SORT = "cr_date-desc"  # второй проход другой сортировкой — ловит то, что «уехало» между страницами

OFFER_COLUMNS = [
    "hash", "url", "section", "mark_code", "mark", "model_code", "model", "generation", "complectation",
    "complectation_id", "year", "mileage", "price", "price_discount", "discount_tradein", "discount_credit",
    "discount_insurance", "discount_max", "engine_volume", "engine_cc", "engine_power", "fuel", "transmission", "drive", "body",
    "color", "color_hex", "vin", "vin_full", "owners", "pts", "customs_cleared", "availability",
    "created_at_source", "fresh_at_source", "generation_id",
]
# поле → тип события при изменении (по данным выдачи)
TRACKED_LISTING = {
    "availability": "availability_changed",
    "mileage": "mileage_changed",
    "complectation_id": "complectation_changed",
}
PHOTO_HASH_MAX_DISTANCE = 6
RELIST_WINDOW_DAYS = 30
RECHECK_REMOVED_HOURS = 12


class DealerMismatch(Exception):
    """Настройки указывают на другой салон, чем тот, для которого ведётся база."""


class Collector:
    def __init__(self, conn, source: Source, settings: Settings,
                 photo_fetch: Callable[[str], bytes] | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.conn = conn
        self.source = source
        self.s = settings
        self.photo_fetch = photo_fetch
        self.clock = clock
        self.run_id: int | None = None
        self.stats: dict = {}
        self._maybe_returned: dict[str, str] = {}
        self.photo_requests = 0

    # ---------------------------------------------------------------- run
    def run(self) -> dict:
        # одна база — один салон: иначе первый прогон по другому салону «снимет» весь прежний склад
        bound = kv_get(self.conn, "dealer_code")
        if bound and bound != self.s.dealer_code:
            raise DealerMismatch(f"база заведена для салона {bound}, а в настройках {self.s.dealer_code}. "
                                 "Для другого салона укажите отдельный DB_PATH.")
        if not bound:
            kv_set(self.conn, "dealer_code", self.s.dealer_code)
            self.conn.commit()
        started = self.clock()
        t0 = time.monotonic()
        # процесс убили посреди прогона — такой прогон честно помечаем прерванным
        self.conn.execute("UPDATE runs SET status='failed', note=COALESCE(note, 'прогон прерван (процесс остановлен)') "
                          "WHERE status='running'")
        prev_active = self.conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]
        cur = self.conn.execute("INSERT INTO runs(started_at, status, prev_active) VALUES(?, 'running', ?)",
                                (iso(started), prev_active))
        self.run_id = cur.lastrowid
        self.conn.commit()
        st = self.stats = {k: 0 for k in ("new_count", "changed_count", "removed_count", "returned_count",
                                          "relisted_count", "sold_count", "cards_fetched", "details_pending")}
        notes: list[str] = []
        blocked = False
        status = "complete"
        try:
            raw_offers, source_total, blocked, listing_note = self._collect_listing()
            if listing_note:
                notes.append(listing_note)
            offers = {oid: parse_offer(raw) for oid, raw in raw_offers.items()}
            offers = {oid: o for oid, o in offers.items()
                      if not o.get("dealer_code") or o["dealer_code"] == self.s.dealer_code}
            collected = len(offers)
            base = max(prev_active, 1)
            completeness = collected / base if prev_active else (1.0 if collected else 0.0)
            enough_vs_prev = prev_active == 0 or collected >= self.s.completeness_threshold * prev_active
            enough_vs_source = not source_total or collected >= self.s.completeness_threshold * source_total
            complete = not blocked and enough_vs_prev and enough_vs_source and collected > 0
            if not enough_vs_prev:
                notes.append(f"собрано {collected} из {prev_active} прошлого склада (< {self.s.completeness_threshold:.0%})"
                             " — снятия не считаем")

            self._upsert_listing(offers)
            # фиксируем по частям: одна транзакция на весь прогон держала базу минутами,
            # и бот получал «database is locked» (живой запуск serve 21.10.2026)
            self.conn.commit()
            blocked_cards = self._fetch_details(offers)
            if blocked_cards:
                notes.append(blocked_cards)
                complete = False

            if complete:
                blocked_verify = self._process_missing(set(offers))
                if blocked_verify:
                    notes.append(blocked_verify)
                    complete = False
            self._detect_relisted()
            self._mark_probably_sold()
            self._mark_duplicates_and_anomalies()
            status = "complete" if complete else "incomplete"
            self._finish(status, source_total, collected, completeness, blocked, t0, notes)
            self.conn.commit()
            return self.summary()
        except SourceFormatError as e:
            self.conn.rollback()
            self.stats = dict.fromkeys(self.stats, 0)
            notes.append(f"СМЕНА ФОРМАТА ИСТОЧНИКА: {e}")
            self._finish("failed", None, 0, 0.0, False, t0, notes)
            self.conn.commit()
            raise
        except Exception as e:  # неожиданное — прогон помечаем, исключение пробрасываем
            self.conn.rollback()
            self.stats = dict.fromkeys(self.stats, 0)
            notes.append(f"ошибка: {e.__class__.__name__}: {e}")
            self._finish("failed", None, 0, 0.0, blocked, t0, notes)
            self.conn.commit()
            raise

    def summary(self) -> dict:
        row = self.conn.execute("SELECT * FROM runs WHERE id=?", (self.run_id,)).fetchone()
        d = dict(row)
        d["photo_requests"] = self.photo_requests
        return d

    def _finish(self, status, source_total, collected, completeness, blocked, t0, notes):
        st = self.stats
        self.conn.execute(
            """UPDATE runs SET finished_at=?, status=?, source_total=?, collected=?, completeness=?, requests=?,
                   errors=?, blocked=?, new_count=?, changed_count=?, removed_count=?, returned_count=?,
                   relisted_count=?, sold_count=?, cards_fetched=?, details_pending=?, duration_s=?, note=?
               WHERE id=?""",
            (iso(self.clock()), status, source_total, collected, round(completeness, 4),
             getattr(self.source, "requests", 0), getattr(self.source, "errors", 0), int(blocked),
             st["new_count"], st["changed_count"], st["removed_count"], st["returned_count"], st["relisted_count"],
             st["sold_count"], st["cards_fetched"], st["details_pending"], round(time.monotonic() - t0, 1),
             "; ".join(notes) or None, self.run_id))

    # ----------------------------------------------------------- listing
    def _collect_listing(self) -> tuple[dict[str, dict], int | None, bool, str | None]:
        """Все страницы новых и с пробегом. Возвращает (id → сырой offer, сколько заявил источник, blocked, заметка)."""
        found: dict[str, dict] = {}
        source_total = 0
        blocked = False
        note = None
        for section in SECTIONS:
            section_ids: set[str] = set()
            section_total = None
            for sort in (None, ALT_SORT):
                page = 1
                while True:
                    try:
                        data = self.source.listing(section, page, sort=sort)
                    except SourceBlocked as e:
                        log.warning("блокировка: %s", e)
                        return found, (source_total or None), True, f"блокировка: {e}"
                    check_listing_response(data)
                    pag = data["pagination"]
                    if section_total is None:
                        section_total = int(pag["total_offers_count"])
                    for raw in data["offers"]:
                        oid = str(raw.get("id"))
                        section_ids.add(oid)
                        found.setdefault(oid, raw)
                    if page >= int(pag["total_page_count"]) or not data["offers"]:
                        break
                    page += 1
                # второй проход нужен, только если первый недобрал до заявленного количества
                if section_total is not None and len(section_ids) >= section_total:
                    break
                if sort is None:
                    log.info("%s: собрано %s из %s, второй проход с другой сортировкой",
                             section, len(section_ids), section_total)
            source_total += section_total or 0
            if section_total and len(section_ids) < section_total:
                note = (note or "") + f"{section}: {len(section_ids)} из {section_total}. "
        return found, source_total, blocked, note

    def _upsert_listing(self, offers: dict[str, dict]) -> None:
        """Выдача — индекс ID и подсказка «что-то поменялось». Цены в ней отстают на дни
        (сверка 21.10.2026: update_date в выдаче 11.10, в карточке — сегодня), поэтому у машин
        с карточкой поля из выдачи не перезаписываются — их обновляет только карточка."""
        now = iso(self.clock())
        for oid, o in offers.items():
            row = self.conn.execute("SELECT status, details_at, listing_sig, removed_at FROM offers WHERE id=?",
                                    (oid,)).fetchone()
            if row is None:
                self._insert_offer(o, listing_signature(o), now)
                self.stats["new_count"] += 1
            elif row["status"] != "active":
                # снова в выдаче — но выдача отстаёт от карточек, поэтому «вернулось» подтверждаем карточкой.
                # Проданные висят в выдаче сутками: перепроверяем, только если запись в выдаче изменилась
                # или с прошлой проверки прошло RECHECK_REMOVED_HOURS (прогон №8: 100 лишних карточек из 171)
                checked = max(filter(None, (row["details_at"], row["removed_at"])), default=None)
                stale = checked is None or parse_iso(checked) <= self.clock() - timedelta(hours=RECHECK_REMOVED_HOURS)
                if row["listing_sig"] != listing_signature(o) or stale:
                    self._maybe_returned[oid] = row["status"]
                self.conn.execute("UPDATE offers SET last_seen=? WHERE id=?", (now, oid))
            elif row["details_at"] is None:
                # карточки ещё нет — держим предварительные данные выдачи, без событий
                self._write_fields(oid, o, extra={"photos": json.dumps(o["photos"]), "photo_count": o["photo_count"]})
                self.conn.execute("UPDATE offers SET last_seen=?, listing_sig=? WHERE id=?",
                                  (now, listing_signature(o), oid))
            else:
                # id поколения не меняется и нужен рынку — дописываем его и машинам, собранным до его появления
                self.conn.execute("UPDATE offers SET last_seen=?, listing_sig=?, generation_id=COALESCE(?, generation_id) "
                                  "WHERE id=?", (now, listing_signature(o), o.get("generation_id"), oid))

    def _write_fields(self, oid: str, o: dict, extra: dict | None = None) -> None:
        cols = {c: o[c] for c in OFFER_COLUMNS}
        cols["vin_full"] = int(bool(o["vin_full"]))
        cols["customs_cleared"] = None if o["customs_cleared"] is None else int(o["customs_cleared"])
        cols.update(extra or {})
        sets = ", ".join(f"{c}=?" for c in cols)
        self.conn.execute(f"UPDATE offers SET {sets} WHERE id=?", (*cols.values(), oid))

    def _diff_tracked(self, oid: str, row, o: dict) -> bool:
        """События по ценам и отслеживаемым полям: старое значение из базы, новое — из карточки."""
        now = iso(self.clock())
        changed = False
        if row["price"] != o["price"] or row["price_discount"] != o["price_discount"]:
            if row["price"] != o["price"]:
                self._event(oid, "price_changed", row["price"], o["price"])
            if row["price_discount"] != o["price_discount"]:
                self._event(oid, "discount_changed", row["price_discount"], o["price_discount"])
            self.conn.execute("INSERT INTO price_history(offer_id, ts, price, price_discount, run_id) VALUES(?,?,?,?,?)",
                              (oid, now, o["price"], o["price_discount"], self.run_id))
            changed = True
        for field, ev in TRACKED_LISTING.items():
            if row[field] != o[field] and o[field] is not None:
                self._event(oid, ev, row[field], o[field])
                changed = True
        return changed

    def _insert_offer(self, o: dict, sig: str, now: str) -> None:
        cols = {c: o[c] for c in OFFER_COLUMNS}
        cols["vin_full"] = int(bool(o["vin_full"]))
        cols["customs_cleared"] = None if o["customs_cleared"] is None else int(o["customs_cleared"])
        cols.update(id=o["id"], photos=json.dumps(o["photos"]), photo_count=o["photo_count"],
                    first_seen=now, last_seen=now, status="active", listing_sig=sig,
                    photo_hash=self._photo_hash(o.get("photo_preview")))
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        self.conn.execute(f"INSERT INTO offers({names}) VALUES({marks})", tuple(cols.values()))
        self.conn.execute("INSERT INTO price_history(offer_id, ts, price, price_discount, run_id) VALUES(?,?,?,?,?)",
                          (o["id"], now, o["price"], o["price_discount"], self.run_id))
        self._event(o["id"], "appeared", None, o["price"], f"{o['mark']} {o['model']} {o['year']}")

    def _photo_hash(self, url: str | None) -> str | None:
        if not url or not self.photo_fetch:
            return None
        try:
            self.photo_requests += 1
            return dhash(self.photo_fetch(url))
        except Exception as e:  # noqa: BLE001 — без хеша фото жить можно
            log.info("хеш фото не посчитан (%s): %s", url[-40:], e)
            return None

    # ----------------------------------------------------------- details
    def _confirm_returned(self, offers: dict[str, dict]) -> str | None:
        for oid, old_status in list(self._maybe_returned.items()):
            o = offers[oid]
            try:
                raw = self.source.card(f"{oid}-{o['hash']}")
            except SourceBlocked as e:
                return f"проверка вернувшихся: блокировка ({e})"
            card = parse_card(raw)
            self.stats["cards_fetched"] += 1
            if card is None or card.get("gone"):
                # в выдаче висит, но карточка снята — остаётся снятым; запоминаем, когда проверяли
                self.conn.execute("UPDATE offers SET details_at=?, listing_sig=? WHERE id=?",
                                  (iso(self.clock()), listing_signature(o), oid))
                continue
            row = self.conn.execute("SELECT removed_at FROM offers WHERE id=?", (oid,)).fetchone()
            self._event(oid, "returned", old_status, "active", f"снова в продаже после {row['removed_at'] or '?'}")
            self.conn.execute("UPDATE offers SET status='active', removed_at=NULL, sold_at=NULL WHERE id=?", (oid,))
            self.stats["returned_count"] += 1
            if card.get("inactive"):
                # снята старым правилом по временному статусу: в продаже с прежними данными, карточка — позже
                self.conn.execute("UPDATE offers SET details_sig=NULL WHERE id=?", (oid,))
                continue
            self._apply_card(oid, card)
        return None

    def _fetch_details(self, offers: dict[str, dict]) -> str | None:
        """Карточки только новым и изменившимся (и тем, что не успели в прошлый раз)."""
        note = self._confirm_returned(offers)
        if note:
            return note
        # 1) нет карточки, 2) в выдаче что-то поменялось, 3) по кругу — самые давно проверенные:
        # выдача отстаёт, и изменение цены в ней можно вообще не увидеть
        need = [r["id"] for r in self.conn.execute(
            "SELECT id FROM offers WHERE status='active' AND (details_at IS NULL OR details_sig IS NULL "
            "OR details_sig != listing_sig) ORDER BY details_at IS NOT NULL, first_seen DESC")]
        need = [oid for oid in need if oid in offers]
        todo, rest = need[: self.s.max_cards_per_run], need[self.s.max_cards_per_run:]
        self.stats["details_pending"] = len(rest)
        room = max(0, min(self.s.refresh_cards_per_run, self.s.max_cards_per_run - len(todo)))
        if room:
            stale = [r["id"] for r in self.conn.execute(
                "SELECT id FROM offers WHERE status='active' AND details_at IS NOT NULL ORDER BY details_at LIMIT ?",
                (room + len(todo),))]
            todo += [oid for oid in stale if oid in offers and oid not in todo][:room]
        for i, oid in enumerate(todo, 1):
            o = offers[oid]
            # каждая карточка — своя короткая транзакция: бот ждёт базу не дольше 30 с, а 20 карточек — это минута
            self.conn.commit()
            if i % 20 == 0:
                log.info("карточки: %s из %s", i, len(todo))
            try:
                raw = self.source.card(f"{oid}-{o['hash']}")
            except SourceBlocked as e:
                self.stats["details_pending"] += len(todo) - todo.index(oid)
                return f"карточки: блокировка ({e}), остальное доделает следующий прогон"
            card = parse_card(raw)
            self.stats["cards_fetched"] += 1
            if card is None or card.get("gone"):
                # выдача отстаёт от карточек: объявление уже снято, хотя ещё в списке
                self._mark_removed(oid, "карточка не найдена" if card is None
                                   else f"статус карточки {card['status_source']}, хотя ещё в выдаче")
                continue
            if card.get("inactive"):
                # временный статус: машина остаётся в продаже с прежними данными, карточку перечитаем позже
                log.info("карточка %s: временный статус %s, не снимаем", oid, card["status_source"])
                continue
            self._apply_card(oid, card)
        return None

    def _apply_card(self, oid: str, card: dict) -> None:
        """Карточка — источник истины по цене, скидкам, наличию, пробегу, описанию и фото."""
        row = self.conn.execute("SELECT * FROM offers WHERE id=?", (oid,)).fetchone()
        had_details = row["details_at"] is not None
        old_photos = json.loads(row["photos"]) if row["photos"] else []
        changed = False
        if not had_details:
            # первая карточка уточняет предварительные данные выдачи — это не «изменение цены»
            self.conn.execute(
                "UPDATE price_history SET price=?, price_discount=? WHERE rowid=(SELECT MIN(rowid) FROM price_history "
                "WHERE offer_id=?)", (card["price"], card["price_discount"], oid))
            if row["price"] != card["price"]:
                self.conn.execute("UPDATE events SET new=? WHERE offer_id=? AND type='appeared'", (card["price"], oid))
        else:
            changed = self._diff_tracked(oid, row, card)
            if (row["description"] or "") != (card["description"] or ""):
                self._event(oid, "description_changed", (row["description"] or "")[:300], (card["description"] or "")[:300])
                changed = True
            if _photo_ids(old_photos) != _photo_ids(card["photos"]):
                self._event(oid, "photos_changed", len(old_photos), len(card["photos"]),
                            json.dumps({"было": len(old_photos), "стало": len(card["photos"])}, ensure_ascii=False))
                changed = True
            if row["complectation"] != card["complectation"] and card["complectation"]:
                self._event(oid, "complectation_changed", row["complectation"], card["complectation"])
                changed = True
        if changed:
            self.stats["changed_count"] += 1
        self._write_fields(oid, card, extra={
            "description": card["description"], "photos": json.dumps(card["photos"]),
            "photo_count": len(card["photos"]), "details_at": iso(self.clock())})
        self.conn.execute("UPDATE offers SET details_sig=listing_sig WHERE id=?", (oid,))
        if row["photo_hash"] is None and card.get("photo_preview"):
            h = self._photo_hash(card["photo_preview"])
            if h:
                self.conn.execute("UPDATE offers SET photo_hash=? WHERE id=?", (h, oid))

    # ----------------------------------------------------------- removals
    def _process_missing(self, seen: set[str]) -> str | None:
        """Активные в базе, но не найденные в выдаче. Снимаем только после проверки карточки."""
        missing = [r for r in self.conn.execute("SELECT id, hash FROM offers WHERE status='active'")
                   if r["id"] not in seen]
        now = iso(self.clock())
        for r in missing[: self.s.max_verify_per_run]:
            try:
                raw = self.source.card(f"{r['id']}-{r['hash']}")
            except SourceBlocked as e:
                return f"проверка снятий: блокировка ({e})"
            card = parse_card(raw)
            if card is not None and card.get("status_source") == "ACTIVE" and \
                    (not card.get("dealer_code") or card["dealer_code"] == self.s.dealer_code):
                # объявление живо — просто не попалось в выдаче (сдвиг страниц)
                self._event(r["id"], "still_active", None, None, "нет в выдаче, но карточка активна")
                self.conn.execute("UPDATE offers SET last_seen=? WHERE id=?", (now, r["id"]))
                continue
            self._mark_removed(r["id"], "карточка не найдена" if card is None
                               else f"статус карточки {card.get('status_source')}")
        return None

    def _mark_removed(self, oid: str, reason: str) -> None:
        self._event(oid, "removed", "active", "removed", reason)
        self.conn.execute("UPDATE offers SET status='removed', removed_at=? WHERE id=?", (iso(self.clock()), oid))
        self.stats["removed_count"] += 1

    def _detect_relisted(self) -> None:
        now = self.clock()
        recent = self.conn.execute(
            "SELECT * FROM offers WHERE status='active' AND relisted_from IS NULL AND first_seen >= ?",
            (iso(now - timedelta(days=7)),)).fetchall()
        if not recent:
            return
        gone = self.conn.execute(
            "SELECT * FROM offers WHERE status IN ('removed','sold') AND removed_at >= ? "
            "AND id NOT IN (SELECT relisted_from FROM offers WHERE relisted_from IS NOT NULL)",
            (iso(now - timedelta(days=RELIST_WINDOW_DAYS)),)).fetchall()
        catalog = self._catalog_hashes()
        used: set[str] = set()
        for new in recent:
            for old in gone:
                # перевыставление — новый ID, появившийся ПОЗЖЕ старого; увиденные в одном прогоне — разные машины
                if old["id"] in used or old["id"] == new["id"] or new["first_seen"] <= old["first_seen"]:
                    continue
                how = relist_match(dict(old), dict(new), catalog)
                if how:
                    used.add(old["id"])
                    self.conn.execute("UPDATE offers SET relisted_from=? WHERE id=?", (old["id"], new["id"]))
                    self._event(new["id"], "relisted", old["id"], new["id"], how)
                    self._event(old["id"], "relisted", old["id"], new["id"], how)
                    self.stats["relisted_count"] += 1
                    break

    def _catalog_hashes(self) -> set[str]:
        # одно и то же фото у нескольких активных машин — каталожная картинка, по ней не сравниваем
        return {r[0] for r in self.conn.execute(
            "SELECT photo_hash FROM offers WHERE status='active' AND photo_hash IS NOT NULL "
            "GROUP BY photo_hash HAVING COUNT(*) > 1")}

    def _mark_probably_sold(self) -> None:
        cutoff = iso(self.clock() - timedelta(days=self.s.sold_after_days))
        rows = self.conn.execute(
            "SELECT id, removed_at FROM offers WHERE status='removed' AND removed_at <= ? "
            "AND id NOT IN (SELECT relisted_from FROM offers WHERE relisted_from IS NOT NULL)", (cutoff,)).fetchall()
        now = iso(self.clock())
        for r in rows:
            self.conn.execute("UPDATE offers SET status='sold', sold_at=? WHERE id=?", (now, r["id"]))
            self._event(r["id"], "probably_sold", "removed", "sold",
                        f"снято {r['removed_at']} и не вернулось за {self.s.sold_after_days} дн.")
            self.stats["sold_count"] += 1

    def _mark_duplicates_and_anomalies(self) -> None:
        rows = [dict(r) for r in self.conn.execute("SELECT * FROM offers WHERE status='active'")]
        medians = nz.model_medians(rows)
        for o in rows:
            an = nz.find_anomalies(o, medians, today=self.clock().date())
            self.conn.execute("UPDATE offers SET anomalies=? WHERE id=?", (json.dumps(an) if an else None, o["id"]))
        # дубли: одна машина в двух активных объявлениях — по VIN или по хешу фото
        rows.sort(key=lambda r: r["first_seen"])
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if b.get("duplicate_of"):
                    continue
                how = duplicate_match(a, b)
                if how:
                    self.conn.execute("UPDATE offers SET duplicate_of=? WHERE id=?", (a["id"], b["id"]))
                    self._event(b["id"], "duplicate", a["id"], b["id"], how)
                    b["duplicate_of"] = a["id"]

    # ----------------------------------------------------------- utils
    def _event(self, offer_id: str, typ: str, old, new, details: str | None = None) -> None:
        self.conn.execute("INSERT INTO events(offer_id, run_id, ts, type, old, new, details) VALUES(?,?,?,?,?,?,?)",
                          (offer_id, self.run_id, iso(self.clock()), typ,
                           None if old is None else str(old), None if new is None else str(new), details))


def _photo_ids(urls: list[str]) -> list[str]:
    # .../get-autoru-vos/<группа>/<имя>/orig → имя; сравниваем набор и порядок
    return [u.rstrip("/").rsplit("/", 2)[-2] for u in urls]


def _vin_compatible(a: str | None, b: str | None) -> bool:
    """Частично скрытые VIN (LFB*****...) совместимы, если совпадают все видимые символы."""
    if not a or not b or len(a) != len(b):
        return True
    return all(x == y or "*" in (x, y) for x, y in zip(a, b))


def _same_car_basics(a: dict, b: dict) -> bool:
    return (a["mark_code"] == b["mark_code"] and a["model_code"] == b["model_code"] and a["year"] == b["year"]
            and a["section"] == b["section"] and _vin_compatible(a.get("vin"), b.get("vin")))


def relist_match(old: dict, new: dict, catalog_hashes: set[str] = frozenset()) -> str | None:
    """Та же машина под новым ID? Возвращает, по какому признаку совпало."""
    if old.get("vin_full") and new.get("vin_full") and old["vin"] == new["vin"]:
        return "VIN"
    if not _same_car_basics(old, new):
        return None
    if new["section"] == "new":
        # Новые машины одной партии одинаковы, фото каталожные, от VIN видно 3 символа.
        # Живой прогон #4 дал так 8 ложных совпадений — для новых признаём только полный VIN.
        return None
    if old.get("photo_hash") and new.get("photo_hash") and new["photo_hash"] not in catalog_hashes and \
            hamming(old["photo_hash"], new["photo_hash"]) <= PHOTO_HASH_MAX_DISTANCE:
        return "хеш первого фото"
    same_spec = all(old.get(k) == new.get(k) for k in
                    ("generation", "complectation_id", "engine_power", "transmission", "drive", "color_hex", "body"))
    if not same_spec:
        return None
    if old["section"] == "used":
        m1, m2 = old.get("mileage") or 0, new.get("mileage") or 0
        # пробег не уменьшается и вырос не больше чем на 3% (+500 км на перегон)
        if m2 >= m1 and m2 - m1 <= max(500, m1 * 0.03):
            return "характеристики + пробег"
        return None
    return None


def duplicate_match(a: dict, b: dict) -> str | None:
    if a.get("vin_full") and b.get("vin_full") and a["vin"] == b["vin"]:
        return "VIN"
    # у новых машин бывают каталожные фото, одинаковые для всей партии, — по фото дубли не ищем
    if a["section"] == "used" and a.get("photo_hash") and b.get("photo_hash") and _same_car_basics(a, b) and \
            hamming(a["photo_hash"], b["photo_hash"]) <= PHOTO_HASH_MAX_DISTANCE // 2:
        return "хеш первого фото"
    return None
