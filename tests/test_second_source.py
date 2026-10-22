"""Второй источник: склад сайта rolf.ru на сохранённых ответах API (22.10.2026) и склейка с Авто.ру."""
import json

import httpx
import pytest

from kiravto.second_source import (RolfSource, match_candidates, match_stock, parse_vehicle, sync_rolf,
                                   vin_compatible)
from kiravto.source.parse import SourceFormatError
from tests.conftest import FIX

PAGES = {t: json.loads((FIX / "rolf" / f"vehicles_{t}_p1.json").read_text(encoding="utf-8")) for t in ("new", "used")}


def fake_client(pages=PAGES, status=200):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        vtype = request.url.params["vehicle_type"]
        body = json.loads(json.dumps(pages[vtype]))
        body["data"]["pagination"]["last_page"] = 1
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def used_vehicle():
    return parse_vehicle(PAGES["used"]["data"]["items"][0])


def offer_like(v, **kw):
    o = {"id": "1", "section": v["section"], "mark_code": v["mark_code"], "model": v["model"], "year": v["year"],
         "mileage": v["mileage"], "price": v["price"], "color": v["color"], "vin": v["vin"][:3] + "*" * 14}
    o.update(kw)
    return o


def test_parse_vehicle_has_full_vin_and_link():
    v = used_vehicle()
    assert v["vin"] == "WAUZZZF45HN038587" and len(v["vin"]) == 17
    assert v["mark_code"] == "AUDI" and v["section"] == "used" and v["mileage"] == 101060
    assert v["url"] == "https://www.rolf.ru/cars/used/audi/a4/27955799/"
    assert v["price_discount"] == v["price"] - 230000


def test_missing_field_is_format_change():
    it = dict(PAGES["used"]["data"]["items"][0])
    it.pop("vin")
    with pytest.raises(SourceFormatError):
        parse_vehicle(it)


def test_vin_compatible():
    assert vin_compatible("WAU**************", "WAUZZZF45HN038587")
    assert not vin_compatible("WBA**************", "WAUZZZF45HN038587")
    assert not vin_compatible(None, "WAUZZZF45HN038587")


def test_used_match_by_vin_prefix_and_mileage():
    v = used_vehicle()
    cands, how = match_candidates(offer_like(v), [v])
    assert cands == [v] and "пробег" in how
    assert match_candidates(offer_like(v, mileage=v["mileage"] + 5000), [v])[0] == []
    assert match_candidates(offer_like(v, vin="XXX**************"), [v])[0] == []


def test_new_batch_ambiguity_is_not_glued(conn):
    v = parse_vehicle(PAGES["new"]["data"]["items"][0])
    twin = dict(v, ext_id="twin", vin=v["vin"][:-1] + "0")     # вторая машина той же партии
    o = offer_like(v)
    assert len(match_candidates(o, [v, twin])[0]) == 2
    conn.execute("INSERT INTO offers(id, hash, url, section, mark_code, model, year, mileage, price, color, vin, "
                 "photos, first_seen, last_seen, status) VALUES('1','h','u',?,?,?,?,0,?,?,?, '[]','t','t','active')",
                 (o["section"], o["mark_code"], o["model"], o["year"], o["price"], o["color"], o["vin"]))
    assert match_stock(conn, [v, twin], "2026-10-22T00:00:00+00:00") == 0
    assert match_stock(conn, [v], "2026-10-22T00:00:00+00:00") == 1
    row = conn.execute("SELECT * FROM offer_sources").fetchone()
    assert row["vin"] == v["vin"] and row["source"] == "rolf" and "цена" in row["matched_by"]


def test_sync_writes_snapshot_and_side_run(conn, settings):
    client, calls = fake_client()
    src = RolfSource("38", pause=(0, 0), client=client)
    r = sync_rolf(conn, settings, src)
    assert r["status"] == "complete" and r["requests"] == 2 == len(calls)
    assert all(c.url.params["dealer_id[0]"] == "38" for c in calls)
    n = conn.execute("SELECT COUNT(*) FROM rolf_stock").fetchone()[0]
    assert n == len(PAGES["new"]["data"]["items"]) + len(PAGES["used"]["data"]["items"])
    assert conn.execute("SELECT kind, status FROM side_runs").fetchone()[:] == ("rolf", "complete")


def test_sync_survives_server_errors(conn, settings, monkeypatch):
    monkeypatch.setattr("kiravto.second_source.time.sleep", lambda s: None)
    client, _ = fake_client(status=503)
    r = sync_rolf(conn, settings, RolfSource("38", pause=(0, 0), client=client))
    assert r["status"] == "incomplete" and r["items"] == 0
    assert conn.execute("SELECT COUNT(*) FROM rolf_stock").fetchone()[0] == 0
