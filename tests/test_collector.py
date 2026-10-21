import copy
import json

import pytest

from kiravto.collector import Collector, relist_match
from kiravto.source.parse import SourceFormatError
from tests.conftest import load_json


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


def test_listing_lags_behind_card_removed(conn, source, settings, clock):
    """Выдача ещё показывает машину, карточка уже REMOVED: снимаем, и статус не «мигает»."""
    stale = source.offers["new"][1]
    removed = load_json("card_removed_1160201304.json")
    source.cards[f"{stale['id']}-{stale['hash']}"] = {**removed, "id": stale["id"]}
    r = run(conn, source, settings, clock)
    assert r["status"] == "complete" and r["removed_count"] == 1
    for _ in range(2):                                   # дальше висит в выдаче — остаётся снятым
        clock.advance(minutes=45)
        r = run(conn, source, settings, clock)
        assert r["returned_count"] == 0
    assert conn.execute("SELECT status FROM offers WHERE id=?", (str(stale["id"]),)).fetchone()[0] == "removed"
    assert len(events(conn, "returned")) == 0
    # а когда карточка снова активна — это настоящий возврат; видим его при перепроверке (раз в 12 ч)
    del source.cards[f"{stale['id']}-{stale['hash']}"]
    clock.advance(minutes=45)
    assert run(conn, source, settings, clock)["returned_count"] == 0     # ещё не время перепроверять
    clock.advance(hours=12)
    assert run(conn, source, settings, clock)["returned_count"] == 1


def test_removed_but_listed_rechecked_rarely(conn, source, settings, clock):
    """Прогон №8: 100 проданных висели в выдаче и каждый прогон тянули свою карточку. Теперь — раз в 12 ч."""
    stale = source.offers["new"][1]
    removed = load_json("card_removed_1160201304.json")
    source.cards[f"{stale['id']}-{stale['hash']}"] = {**removed, "id": stale["id"]}
    run(conn, source, settings, clock)
    ref = f"{stale['id']}-{stale['hash']}"
    for _ in range(3):
        source.card_calls.clear()
        clock.advance(minutes=45)
        run(conn, source, settings, clock)
        assert ref not in source.card_calls
    clock.advance(hours=12)
    source.card_calls.clear()
    run(conn, source, settings, clock)
    assert ref in source.card_calls
    # изменилась запись в выдаче — перепроверяем сразу
    stale["price_info"]["price"] += 1000
    source.card_calls.clear()
    clock.advance(minutes=45)
    run(conn, source, settings, clock)
    assert ref in source.card_calls


def test_failed_run_counters_do_not_lie(conn, source, settings, clock):
    del source.offers["used"][2]["price_info"]
    with pytest.raises(SourceFormatError):
        run(conn, source, settings, clock)
    row = conn.execute("SELECT new_count, cards_fetched FROM runs").fetchone()
    assert tuple(row) == (0, 0)


def test_killed_run_marked_failed(conn, source, settings, clock):
    conn.execute("INSERT INTO runs(started_at, status) VALUES('2026-10-20T08:00:00+00:00', 'running')")
    conn.commit()
    run(conn, source, settings, clock)
    row = conn.execute("SELECT status, note FROM runs WHERE id=1").fetchone()
    assert row["status"] == "failed" and "прерван" in row["note"]


def test_same_batch_new_cars_are_not_relisted(conn, source, settings, clock):
    """Живой прогон #4: две новые RAV4 одной партии (каталожное фото, VIN LFM***) — это не перевыставление."""
    base = {"mark_code": "TOYOTA", "model_code": "RAV_4", "year": 2026, "section": "new", "vin": "LFM**************",
            "vin_full": False, "generation": "VI", "complectation_id": "1", "engine_power": 171,
            "transmission": "вариатор", "drive": "полный", "color_hex": "040001", "body": "Внедорожник 5 дв.",
            "mileage": 0, "photo_hash": "85cc5c5ae2f0d0ac"}
    assert relist_match(base, dict(base), catalog_hashes={"85cc5c5ae2f0d0ac"}) is None
    assert relist_match(base, dict(base)) is None                    # даже если фото кажется уникальным
    full = {**base, "vin": "LFMAAAAAAAAAAAAAA", "vin_full": True}
    assert relist_match(full, dict(full)) == "VIN"


def test_offers_seen_in_same_run_never_relisted(conn, source, settings, clock):
    # копия машины в той же выдаче + снятый «оригинал» — увидены одновременно, значит это две машины
    twin = copy.deepcopy(source.offers["used"][0])
    twin["id"], twin["hash"] = "1999999998", "eeeeeeee"
    source.offers["used"].append(twin)
    orig = source.offers["used"][0]
    removed = load_json("card_removed_1160201304.json")
    source.cards[f"{orig['id']}-{orig['hash']}"] = {**removed, "id": orig["id"]}
    r = run(conn, source, settings, clock)
    assert r["removed_count"] == 1 and r["relisted_count"] == 0


def _set_card_price(source, offer, new_price):
    card = copy.deepcopy(offer)
    card["price_info"]["price"] = new_price
    card.setdefault("description", "x")
    source.cards[f"{offer['id']}-{offer['hash']}"] = card


def test_stale_listing_price_ignored_card_wins(conn, source, settings, clock):
    """Сверка 21.10.2026: выдача отдаёт цену 11.10, карточка и сайт — актуальную.
    Сервис должен держать цену карточки, а при обходе по кругу поймать изменение, которого выдача не видит."""
    o = source.offers["new"][2]
    old_price = o["price_info"]["price"]
    run(conn, source, settings, clock)
    _set_card_price(source, o, old_price + 200_000)       # на сайте подорожала, выдача молчит
    settings.refresh_cards_per_run = 50
    clock.advance(minutes=45)
    r = run(conn, source, settings, clock)
    assert r["changed_count"] >= 1
    assert conn.execute("SELECT price FROM offers WHERE id=?", (str(o["id"]),)).fetchone()[0] == old_price + 200_000
    ev = events(conn, "price_changed")
    assert [(int(e["old"]), int(e["new"])) for e in ev] == [(old_price, old_price + 200_000)]
    # следующий прогон: в выдаче всё та же старая цена — откатываться назад нельзя
    clock.advance(minutes=45)
    run(conn, source, settings, clock)
    assert conn.execute("SELECT price FROM offers WHERE id=?", (str(o["id"]),)).fetchone()[0] == old_price + 200_000
    assert len(events(conn, "price_changed")) == 1


def test_first_card_corrects_listing_price_without_fake_drop(conn, source, settings, clock):
    o = source.offers["used"][6]
    _set_card_price(source, o, o["price_info"]["price"] - 300_000)   # выдача устарела уже при первом знакомстве
    run(conn, source, settings, clock)
    assert events(conn, "price_changed") == []                          # это не «снижение цены»
    hist = conn.execute("SELECT price FROM price_history WHERE offer_id=?", (str(o["id"]),)).fetchall()
    assert [h[0] for h in hist] == [o["price_info"]["price"] - 300_000]


def test_refresh_rotation_is_bounded(conn, source, settings, clock):
    run(conn, source, settings, clock)
    settings.refresh_cards_per_run = 5
    source.card_calls.clear()
    clock.advance(minutes=45)
    assert run(conn, source, settings, clock)["cards_fetched"] == 5     # бережно: только 5 самых давних
    first = set(source.card_calls)
    source.card_calls.clear()
    clock.advance(minutes=45)
    run(conn, source, settings, clock)
    assert not first & set(source.card_calls)                           # по кругу, а не одни и те же


def test_other_dealer_refuses_to_touch_history(conn, source, settings, clock):
    from kiravto.collector import DealerMismatch
    run(conn, source, settings, clock)
    settings.dealer_code = "fresh_x_syndicate_moskva"
    with pytest.raises(DealerMismatch, match="отдельный DB_PATH"):
        run(conn, source, settings, clock)
    assert conn.execute("SELECT COUNT(*) FROM offers WHERE status!='active'").fetchone()[0] == 0
