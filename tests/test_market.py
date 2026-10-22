"""«Ниже рынка» на сохранённом ответе поиска Авто.ру (Mazda CX-5 II с пробегом, 22.10.2026)."""
import copy
import json
from statistics import median

import pytest

from kiravto import market as M
from kiravto import posts as P
from kiravto.source.autoru import check_search_echo
from kiravto.source.parse import SourceBlocked, SourceFormatError
from tests.conftest import FIX

SAMPLE = json.loads((FIX / "market" / "listing_mazda_cx5_used.json").read_text(encoding="utf-8"))


def one_page(data):
    d = copy.deepcopy(data)
    d["pagination"]["total_page_count"] = 1    # в файле 20 машин первой страницы из 61
    return d


class FakeMarket:
    def __init__(self, data=None):
        self.data = data or one_page(SAMPLE)
        self.requests = 0
        self.calls = []
        self.block = False

    def search(self, flt, page=1):
        self.requests += 1
        self.calls.append((flt, page))
        if self.block:
            raise SourceBlocked("тест: капча")
        d = copy.deepcopy(self.data)
        check_search_echo(d, flt)
        return d


def add_offer(conn, oid="1160058037", price=2_649_000, mileage=121_296, year=2020, section="used",
              gen="20939955", now="2026-10-22T10:00:00+00:00"):
    conn.execute(
        "INSERT INTO offers(id, hash, url, section, mark_code, mark, model_code, model, generation, generation_id, "
        "year, mileage, price, price_discount, photos, first_seen, last_seen, status) "
        "VALUES(?, 'h', ?, ?, 'MAZDA', 'Mazda', 'CX_5', 'CX-5', 'II', ?, ?, ?, ?, ?, '[]', ?, ?, 'active')",
        (oid, f"https://auto.ru/cars/used/sale/mazda/cx_5/{oid}-h/", section, gen, year, mileage, price, price,
         now, now))
    conn.commit()


def expected_median(price_km=121_296, year=2020):
    prices = [o["price_info"]["price"] for o in SAMPLE["offers"]
              if (o.get("salon") or {}).get("code") != "rolf_yasenevo_moskva"
              and abs(o["documents"]["year"] - year) <= 1
              and abs(o["state"]["mileage"] - price_km) <= price_km * 0.2]
    return median(prices), len(prices)


def test_own_dealer_excluded_from_comps(settings):
    items = M.parse_comps(SAMPLE, settings.dealer_code)
    own = [o for o in SAMPLE["offers"] if (o.get("salon") or {}).get("code") == settings.dealer_code]
    assert own, "в сохранённом ответе есть машина самого салона — проверка имеет смысл"
    assert len(items) == len(SAMPLE["offers"]) - len(own)
    assert all(i[4] != settings.dealer_code for i in items)


def test_scan_prices_offer_against_median(conn, settings, clock):
    add_offer(conn)
    src = FakeMarket()
    r = M.MarketScanner(conn, src, settings, clock=clock).run()
    assert r["status"] == "complete" and r["groups"] == 1 and r["priced"] == 1
    flt = src.calls[0][0]
    assert flt["catalog_filter"] == [{"mark": "MAZDA", "model": "CX_5", "generation": "20939955"}]
    assert flt["year_from"] == 2019 and flt["year_to"] == 2021
    assert flt["km_age_from"] == int(121_296 * 0.8) and flt["km_age_to"] == int(121_296 * 1.2) + 1
    o = conn.execute("SELECT * FROM offers").fetchone()
    med, n = expected_median()
    assert o["market_n"] == n and o["market_median"] == int(med)
    assert o["market_pct"] == round((2_649_000 - med) / med * 100, 1)
    side = conn.execute("SELECT * FROM side_runs").fetchone()
    assert side["kind"] == "market" and side["requests"] == src.requests == 1


def test_fresh_group_is_not_searched_again(conn, settings, clock):
    add_offer(conn)
    src = FakeMarket()
    M.MarketScanner(conn, src, settings, clock=clock).run()
    clock.advance(hours=5)
    r = M.MarketScanner(conn, src, settings, clock=clock).run()
    assert r["groups"] == 0 and src.requests == 1           # выборка живёт market_ttl_hours
    clock.advance(hours=settings.market_ttl_hours)
    M.MarketScanner(conn, src, settings, clock=clock).run()
    assert src.requests == 2


def test_new_car_out_of_range_triggers_refetch(conn, settings, clock):
    add_offer(conn)
    src = FakeMarket()
    M.MarketScanner(conn, src, settings, clock=clock).run()
    add_offer(conn, oid="2", mileage=40_000, year=2021)       # пробег вне прошлой выборки
    assert len(M.plan_groups(conn, clock(), settings.market_ttl_hours)) == 1


def test_same_car_on_two_pages_counted_once(conn, settings, clock):
    add_offer(conn)
    src = FakeMarket(copy.deepcopy(SAMPLE))                  # две страницы, источник отдал одно и то же
    r = M.MarketScanner(conn, src, settings, clock=clock).run()
    assert src.requests == 2 and r["priced"] == 1
    _, n = expected_median()
    assert conn.execute("SELECT market_n FROM offers").fetchone()[0] == n


def test_too_few_comps_gives_no_estimate(conn, settings, clock):
    add_offer(conn, mileage=1_000_000)                        # таких пробегов в выборке нет
    M.MarketScanner(conn, FakeMarket(), settings, clock=clock).run()
    o = conn.execute("SELECT * FROM offers").fetchone()
    assert o["market_pct"] is None and o["market_median"] is None and o["market_n"] < settings.market_min_comps


def test_ignored_filter_is_format_change_not_silent_country_median(conn, settings, clock):
    add_offer(conn)
    broken = copy.deepcopy(SAMPLE)
    broken["search_parameters"].pop("catalog_filter")       # источник молча проигнорировал фильтр
    r = M.MarketScanner(conn, FakeMarket(one_page(broken)), settings, clock=clock).run()
    assert r["status"] == "failed" and "СМЕНА ФОРМАТА" in r["note"]
    assert conn.execute("SELECT COUNT(*) FROM market_comps").fetchone()[0] == 0
    assert conn.execute("SELECT market_pct FROM offers").fetchone()[0] is None


def test_block_marks_market_run_incomplete(conn, settings, clock):
    add_offer(conn)
    src = FakeMarket()
    src.block = True
    r = M.MarketScanner(conn, src, settings, clock=clock).run()
    assert r["status"] == "incomplete" and r["groups"] == 0 and r["groups_left"] == 1


def test_search_echo_check():
    with pytest.raises(SourceFormatError):
        check_search_echo({"search_parameters": {"section": "used"}}, {"section": "used", "year_from": 2019})
    check_search_echo(SAMPLE, {"section": "used", "year_from": 2019, "catalog_filter": []})


def test_market_line_and_post_filter(conn, settings, clock):
    med, _ = expected_median()
    cheap = int(med * 0.9)
    add_offer(conn, oid="10", price=cheap)
    add_offer(conn, oid="11", price=int(med * 1.1))
    M.MarketScanner(conn, FakeMarket(), settings, clock=clock).run()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM offers")}
    assert rows["10"]["market_pct"] == pytest.approx(-10, abs=0.1)
    line = M.market_line(rows["10"])
    assert line.startswith("📊 ниже рынка на 10%") and "None" not in line
    assert M.market_line(rows["11"]).startswith("📊 выше рынка на 10%")
    assert M.passes_filter(rows["10"], 5) and not M.passes_filter(rows["11"], 5)
    assert M.passes_filter(rows["11"], None)                 # фильтр выключен — проходят все
    assert not M.passes_filter({"market_pct": None}, 5)     # рынок не посчитан — в сообщество не берём
    text = P.drop_post(rows["10"] | {"photos": []}, cheap + 300_000, cheap)["text"]
    assert "ниже рынка на 10%" in text and "None" not in text


def test_drop_posts_respect_market_filter(conn, settings, clock):
    med, _ = expected_median()
    add_offer(conn, oid="10", price=int(med * 0.9))
    add_offer(conn, oid="11", price=int(med * 1.1))
    M.MarketScanner(conn, FakeMarket(), settings, clock=clock).run()
    ts = clock().isoformat(timespec="seconds")
    for oid, price in (("10", int(med * 0.9)), ("11", int(med * 1.1))):
        conn.execute("INSERT INTO events(offer_id, run_id, ts, type, old, new) VALUES(?, 1, ?, 'price_changed', ?, ?)",
                     (oid, ts, price + 500_000, price))
    conn.commit()
    created = P.create_drop_posts(conn, 3, now=clock(), market_below_pct=5)
    ids = [json.loads(r["offer_ids"])[0] for r in conn.execute("SELECT offer_ids FROM posts")]
    assert len(created) == 1 and ids == ["10"]


NEW_LIST = json.loads((FIX / "market" / "listing_toyota_highlander_new_list.json").read_text(encoding="utf-8"))


def add_new_highlander(conn, price=5_690_000):
    conn.execute(
        "INSERT INTO offers(id, hash, url, section, mark_code, mark, model_code, model, generation_id, year, mileage, "
        "price, price_discount, photos, first_seen, last_seen, status) VALUES('n1', 'h', 'u', 'new', 'TOYOTA', "
        "'Toyota', 'HIGHLANDER', 'Highlander', '22453958', 2026, 0, ?, ?, '[]', 't', 't', 'active')", (price, price))
    conn.commit()


def test_new_cars_compared_without_mileage(conn, settings, clock):
    add_new_highlander(conn)
    src = FakeMarket(one_page(NEW_LIST))
    r = M.MarketScanner(conn, src, settings, clock=clock).run()
    assert r["priced"] == 1
    assert "km_age_from" not in src.calls[0][0]                 # у новых пробег не сравниваем
    prices = [o["price_info"]["price"] for o in NEW_LIST["offers"]
              if (o.get("salon") or {}).get("code") != settings.dealer_code and abs(o["documents"]["year"] - 2026) <= 1]
    o = conn.execute("SELECT * FROM offers").fetchone()
    assert o["market_n"] == len(prices) and o["market_median"] == int(median(prices))


def test_grouped_card_is_not_a_car(conn, settings, clock):
    """Свёрнутая выдача новых: одна карточка «133 предложения» не должна стать медианой рынка."""
    add_new_highlander(conn)
    grouped = one_page(NEW_LIST)
    for o in grouped["offers"]:
        o["groupping_info"] = {"size": 133}
    M.MarketScanner(conn, FakeMarket(grouped), settings, clock=clock).run()
    assert conn.execute("SELECT market_pct FROM offers").fetchone()[0] is None
