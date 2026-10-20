import copy
import json

import pytest

from kiravto.collector import Collector, relist_match
from kiravto.source.parse import SourceFormatError


def run(conn, source, settings, clock, **kw):
    return Collector(conn, source, settings, clock=clock, **kw).run()


def events(conn, typ):
    return conn.execute("SELECT * FROM events WHERE type=? ORDER BY id", (typ,)).fetchall()


def test_first_run_collects_everything(conn, source, settings, clock):
    r = run(conn, source, settings, clock)
    assert r["status"] == "complete"
    assert r["collected"] == 22 and r["new_count"] == 22
    assert r["cards_fetched"] == 22                      # подробности для всех новых
    n = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active' AND details_at IS NOT NULL").fetchone()[0]
    assert n == 22
    assert r["requests"] == source.requests


def test_second_run_fetches_cards_only_for_changed(conn, source, settings, clock):
    run(conn, source, settings, clock)
    victim = source.offers["used"][3]
    victim["price_info"]["price"] -= 100_000
    source.card_calls.clear()
    clock.advance(minutes=45)
    r = run(conn, source, settings, clock)
    assert r["cards_fetched"] == 1
    assert source.card_calls == [f"{victim['id']}-{victim['hash']}"]
    ev = events(conn, "price_changed")
    assert len(ev) == 1 and int(ev[0]["old"]) - int(ev[0]["new"]) == 100_000
    hist = conn.execute("SELECT COUNT(*) FROM price_history WHERE offer_id=?", (str(victim["id"]),)).fetchone()[0]
    assert hist == 2


def test_block_mid_run_does_not_remove_anything(conn, source, settings, clock):
    run(conn, source, settings, clock)
    clock.advance(minutes=45)
    source.block_after = source.requests + 2           # пару страниц отдали — и капча
    r = run(conn, source, settings, clock)
    assert r["status"] == "incomplete" and r["blocked"] == 1
    assert conn.execute("SELECT COUNT(*) FROM offers WHERE status!='active'").fetchone()[0] == 0
    assert events(conn, "removed") == []
    # следующий прогон без блокировки доделывает
    source.block_after = None
    clock.advance(minutes=45)
    assert run(conn, source, settings, clock)["status"] == "complete"


def test_less_than_90_percent_is_incomplete(conn, source, settings, clock):
    run(conn, source, settings, clock)
    for _ in range(5):                                  # пропало 5 из 22 → 77%
        source.offers["used"].pop()
    clock.advance(minutes=45)
    r = run(conn, source, settings, clock)
    assert r["status"] == "incomplete"
    assert events(conn, "removed") == []


def test_missing_offer_removed_only_after_card_check(conn, source, settings, clock):
    run(conn, source, settings, clock)
    gone = source.remove(str(source.offers["used"][0]["id"]))     # карточки больше нет
    hidden = source.offers["used"][1]                              # нет в выдаче, но карточка жива
    source.cards[f"{hidden['id']}-{hidden['hash']}"] = {**copy.deepcopy(hidden), "description": "x"}
    source.offers["used"].remove(hidden)
    clock.advance(minutes=45)
    r = run(conn, source, settings, clock)
    assert r["status"] == "complete" and r["removed_count"] == 1
    st = dict(conn.execute("SELECT id, status FROM offers").fetchall())
    assert st[str(gone["id"])] == "removed"
    assert st[str(hidden["id"])] == "active"
    assert len(events(conn, "still_active")) == 1


def test_returned_relisted_and_sold(conn, source, settings, clock):
    settings.completeness_threshold = 0.8   # на 22 машинах три снятия — это 14%, при пороге 90% прогон неполный
    run(conn, source, settings, clock)
    back = source.remove(str(source.offers["used"][0]["id"]))
    relist_old = source.remove(str(source.offers["used"][0]["id"]))
    sold = source.remove(str(source.offers["used"][0]["id"]))
    clock.advance(hours=1)
    assert run(conn, source, settings, clock)["removed_count"] == 3

    # та же машина под новым ID, пробег чуть вырос
    relist_new = copy.deepcopy(relist_old)
    relist_new["id"] = "9" + str(relist_old["id"])[1:]
    relist_new["hash"] = "abcdef12"
    relist_new["url"] = relist_old["url"].replace(str(relist_old["id"]), relist_new["id"]).replace(relist_old["hash"], "abcdef12")
    relist_new["state"]["mileage"] += 120
    source.offers["used"].append(relist_new)
    source.offers["used"].append(back)               # вернулось с тем же ID
    clock.advance(days=1)
    r = run(conn, source, settings, clock)
    assert r["returned_count"] == 1 and r["relisted_count"] == 1
    row = conn.execute("SELECT relisted_from FROM offers WHERE id=?", (relist_new["id"],)).fetchone()
    assert row["relisted_from"] == str(relist_old["id"])

    clock.advance(days=settings.sold_after_days + 1)
    r = run(conn, source, settings, clock)
    st = dict(conn.execute("SELECT id, status FROM offers").fetchall())
    assert st[str(sold["id"])] == "sold"
    assert st[str(relist_old["id"])] == "removed"        # перевыставленная не «продана»
    assert st[str(back["id"])] == "active"
    assert r["sold_count"] == 1


def test_format_change_fails_run_loudly(conn, source, settings, clock):
    del source.offers["used"][2]["price_info"]
    with pytest.raises(SourceFormatError):
        run(conn, source, settings, clock)
    row = conn.execute("SELECT status, note FROM runs ORDER BY id DESC").fetchone()
    assert row["status"] == "failed" and "price_info" in row["note"]
    assert conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0   # мусор не записан


def test_description_and_photo_changes_tracked(conn, source, settings, clock):
    run(conn, source, settings, clock)
    o = source.offers["used"][4]
    card = copy.deepcopy(o)
    card["description"] = "Новое описание"
    card["state"]["image_urls"] = card["state"]["image_urls"][1:]
    card["state"]["images_count"] -= 1
    o["state"]["images_count"] -= 1                  # выдача тоже видит, что фото стало меньше
    source.cards[f"{o['id']}-{o['hash']}"] = card
    clock.advance(minutes=45)
    run(conn, source, settings, clock)
    assert len(events(conn, "description_changed")) == 1
    assert len(events(conn, "photos_changed")) == 1


def test_duplicates_by_photo_hash(conn, source, settings, clock):
    dup = copy.deepcopy(source.offers["used"][5])
    dup["id"] = "1999999999"
    dup["hash"] = "dddddddd"
    source.offers["used"].append(dup)
    hashes = {}

    def fetch(url):  # один и тот же «кадр» у двух объявлений
        return url.encode()

    import kiravto.collector as col
    orig = col.dhash
    col.dhash = lambda b: hashes.setdefault(b, f"{len(hashes):016x}")
    try:
        run(conn, source, settings, clock, photo_fetch=fetch)
    finally:
        col.dhash = orig
    row = conn.execute("SELECT duplicate_of FROM offers WHERE id='1999999999'").fetchone()
    assert row["duplicate_of"] == str(source.offers["used"][5]["id"])


def test_relist_match_rules():
    base = {"mark_code": "KIA", "model_code": "RIO", "year": 2020, "section": "used", "vin": "XWE**************",
            "vin_full": False, "generation": "IV", "complectation_id": "1", "engine_power": 123,
            "transmission": "автомат", "drive": "передний", "color_hex": "040001", "body": "Седан",
            "mileage": 50_000, "photo_hash": None}
    assert relist_match(base, {**base, "mileage": 50_300}) == "характеристики + пробег"
    assert relist_match(base, {**base, "mileage": 40_000}) is None            # пробег не скручивается
    assert relist_match(base, {**base, "color_hex": "FAFBFB"}) is None
    assert relist_match(base, {**base, "vin": "Z94**************"}) is None   # видимая часть VIN другая
    assert relist_match({**base, "vin": "XWEAAAAAAAAAAAAAA", "vin_full": True},
                        {**base, "year": 2019, "vin": "XWEAAAAAAAAAAAAAA", "vin_full": True}) == "VIN"
