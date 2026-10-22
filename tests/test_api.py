from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from kiravto.api import create_app
from kiravto.collector import Collector
from kiravto.db import iso

TOKEN = "secret-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def run(conn, source, settings, clock):
    return Collector(conn, source, settings, clock=clock).run()


def client(settings, clock):
    return TestClient(create_app(settings, clock=clock), raise_server_exceptions=False)


@pytest.fixture
def collected(conn, source, settings, clock):
    """Два полных прогона: во втором у одной машины цена упала на 10%."""
    run(conn, source, settings, clock)
    victim = source.offers["used"][3]
    victim["price_info"]["price"] = int(victim["price_info"]["price"] * 0.9)
    clock.advance(minutes=45)
    run(conn, source, settings, clock)
    return victim


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code and body["error"]["message"]
    return body["error"]


# ------------------------------------------------------------------ health
def test_health_without_token_no_data(settings, clock, conn):
    r = client(settings, clock).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "no_data" and r.json()["last_success_at"] is None


def test_health_ok(collected, settings, clock, conn):
    h = client(settings, clock).get("/health").json()
    assert h["status"] == "ok" and h["active_offers"] == 22
    assert h["last_run"]["collected"] == 22 and h["last_run"]["status"] == "complete"
    assert h["last_success_at"] == iso(clock())


def test_health_stale(collected, settings, clock):
    clock.advance(minutes=3 * settings.run_interval_min + 1)
    h = client(settings, clock).get("/health").json()
    assert h["status"] == "stale" and h["problems"]


def test_health_degraded_reflects_last_success_not_last_run(collected, conn, source, settings, clock):
    success_at = iso(clock())
    for _ in range(2):                                  # два прогона подряд упёрлись в капчу
        clock.advance(minutes=10)
        source.block_after = source.requests + 1
        assert run(conn, source, settings, clock)["status"] == "incomplete"
    h = client(settings, clock).get("/health").json()
    assert h["status"] == "degraded"
    assert h["last_success_at"] == success_at            # не время последнего (неполного) прогона
    assert h["last_run"]["status"] == "incomplete"


# -------------------------------------------------------------------- auth
@pytest.mark.parametrize("path", ["/offers", "/offers/1", "/drops", "/stock", "/runs", "/report"])
def test_protected_without_token(settings, clock, conn, path):
    assert_error(client(settings, clock).get(path), 401, "unauthorized")


def test_wrong_token_and_both_headers(collected, settings, clock):
    c = client(settings, clock)
    assert_error(c.get("/offers", headers={"Authorization": "Bearer nope"}), 401, "unauthorized")
    assert_error(c.get("/offers", headers={"X-API-Token": "nope"}), 401, "unauthorized")
    assert c.get("/offers", headers={"X-API-Token": TOKEN}).status_code == 200
    assert c.get("/offers", headers=AUTH).status_code == 200


def test_no_token_configured_closes_api(settings, clock, conn):
    settings.api_token = ""
    c = client(settings, clock)
    err = assert_error(c.get("/offers", headers={"Authorization": "Bearer "}), 503, "token_not_configured")
    assert "API_TOKEN не задан" in err["message"]
    assert_error(c.get("/stock", headers={"X-API-Token": "anything"}), 503, "token_not_configured")
    assert c.get("/health").status_code == 200


# ------------------------------------------------------------------ offers
def count(conn, sql, *args):
    return conn.execute(f"SELECT COUNT(*) FROM offers WHERE status='active' AND {sql}", args).fetchone()[0]


@pytest.mark.parametrize("mark", ["Мерседес", "MB", "mercedes-benz", "мб"])
def test_mark_aliases_resolve(collected, conn, settings, clock, mark):
    r = client(settings, clock).get("/offers", params={"mark": mark}, headers=AUTH).json()
    assert r["filters"]["mark_code"] == "MERCEDES"
    assert r["total"] == count(conn, "mark_code='MERCEDES'") == len(r["items"])
    assert all(i["mark"] == "Mercedes-Benz" for i in r["items"])


def test_filters(collected, conn, settings, clock):
    c = client(settings, clock)
    get = lambda **p: c.get("/offers", params=p, headers=AUTH).json()  # noqa: E731
    assert get()["total"] == 22
    assert get(section="new")["total"] == count(conn, "section='new'") == 8
    assert get(section="used")["total"] == 14
    r = get(price_min=2_000_000, price_max=3_000_000, sort="-price")
    assert r["total"] == count(conn, "price BETWEEN 2000000 AND 3000000")
    prices = [i["price"] for i in r["items"]]
    assert prices == sorted(prices, reverse=True) and all(2_000_000 <= p <= 3_000_000 for p in prices)
    r = get(year_min=2020, year_max=2022)
    assert r["total"] == count(conn, "year BETWEEN 2020 AND 2022")
    assert all(2020 <= i["year"] <= 2022 for i in r["items"])
    assert get(availability="в наличии")["total"] == get(availability="in_stock")["total"] \
        == count(conn, "availability='IN_STOCK'") == 20
    assert get(availability="IN_TRANSIT")["total"] == 2
    assert get(mark="тойота", model="Camry")["total"] == 2
    page = get(limit=5, offset=5)
    assert len(page["items"]) == 5 and page["total"] == 22
    assert get(status="removed")["total"] == 0 and get(status="all")["total"] == 22


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"section": "old"}, {"status": "gone"},
                                    {"sort": "random"}, {"price_min": "дорого"}, {"offset": -1},
                                    {"price_min": 5, "price_max": 1}])
def test_validation_error_shape(collected, settings, clock, params):
    err = assert_error(client(settings, clock).get("/offers", params=params, headers=AUTH), 422, "validation_error")
    assert isinstance(err["details"], list) and err["details"][0]["where"].startswith("query.")


def test_offer_and_history(collected, settings, clock):
    c = client(settings, clock)
    oid = str(collected["id"])
    o = c.get(f"/offers/{oid}", headers=AUTH).json()
    assert o["id"] == oid and o["price"] == collected["price_info"]["price"]
    h = c.get(f"/offers/{oid}/history", headers=AUTH).json()
    types = [e["type"] for e in h["events"]]
    assert len(h["prices"]) == 2 and types[0] == "appeared" and "price_changed" in types
    err = assert_error(c.get("/offers/42", headers=AUTH), 404, "not_found")
    assert "42" in err["message"]
    assert_error(c.get("/offers/42/history", headers=AUTH), 404, "not_found")
    assert_error(c.get("/nowhere", headers=AUTH), 404, "not_found")


# --------------------------------------------------------------- аналитика
def test_drops(collected, settings, clock):
    c = client(settings, clock)
    r = c.get("/drops", headers=AUTH).json()
    assert r["total"] == 1
    d = r["items"][0]
    old = round(collected["price_info"]["price"] / 0.9)
    assert d["id"] == str(collected["id"]) and d["new_price"] == collected["price_info"]["price"]
    assert abs(d["old_price"] - old) <= 1 and d["drop_pct"] == pytest.approx(10.0, abs=0.01)
    assert c.get("/drops", params={"min_pct": 15}, headers=AUTH).json()["total"] == 0
    clock.advance(days=8)                                 # событие вышло из окна
    assert c.get("/drops", params={"days": 7}, headers=AUTH).json()["total"] == 0


def test_stock_and_runs(collected, settings, clock):
    c = client(settings, clock)
    s = c.get("/stock", headers=AUTH).json()
    assert s["active"] == 22 and s["by_section"] == {"new": 8, "used": 14} and s["price_changes_7d"] == 1
    runs = c.get("/runs", params={"limit": 1}, headers=AUTH).json()["items"]
    assert len(runs) == 1 and runs[0]["id"] == 2
    assert_error(c.get("/runs", params={"limit": 0}, headers=AUTH), 422, "validation_error")


def test_report_endpoint(collected, conn, settings, clock):
    c = client(settings, clock)
    assert_error(c.get("/report", headers=AUTH), 404, "not_found")
    from kiravto.report import render_report
    render_report(conn, settings.report_path, now=clock(), settings=settings)
    r = c.get("/report", headers=AUTH)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Склад РОЛЬФ Ясенево" in r.text


def test_internal_error_shape(settings, clock):
    def broken():
        raise RuntimeError("база недоступна")
    c = TestClient(create_app(settings, conn_factory=broken, clock=clock), raise_server_exceptions=False)
    assert_error(c.get("/stock", headers=AUTH), 500, "internal_error")


def test_openapi_lists_everything(settings, clock):
    spec = client(settings, clock).get("/openapi.json").json()
    assert spec["info"]["title"] == "Киравто: склад РОЛЬФ Ясенево"
    assert set(spec["paths"]) == {"/health", "/offers", "/offers/{offer_id}", "/offers/{offer_id}/history",
                                  "/drops", "/stock", "/runs", "/report", "/market", "/second-source"}
    for path, ops in spec["paths"].items():
        assert ops["get"]["summary"] and ops["get"]["description"], path
    params = {p["name"]: p for p in spec["paths"]["/offers"]["get"]["parameters"]}
    assert {"mark", "model", "section", "price_min", "price_max", "year_min", "year_max", "availability",
            "status", "sort", "limit", "offset"} <= set(params)
    assert all(p.get("description") for p in params.values())
    assert "Мерседес" in params["mark"]["description"]


def test_market_and_second_source(collected, conn, settings, clock):
    oid = str(collected["id"])
    conn.execute("UPDATE offers SET market_pct=-12.5, market_median=1000000, market_n=9 WHERE id=?", (oid,))
    conn.execute("INSERT INTO offer_sources(offer_id, source, ext_id, url, vin, price, matched_by, first_seen, last_seen) "
                 "VALUES(?, 'rolf', '77', 'https://www.rolf.ru/cars/used/x/y/77/', 'WAUZZZF45HN038587', 1, 'тест', 't', 't')",
                 (oid,))
    conn.commit()
    c = client(settings, clock)
    m = c.get("/market", params={"min_pct": 10}, headers=AUTH).json()
    assert [i["id"] for i in m["items"]] == [oid] and m["coverage"]["with_estimate"] == 1
    assert c.get("/market", params={"min_pct": 20}, headers=AUTH).json()["items"] == []
    assert_error(c.get("/market", params={"section": "moto"}, headers=AUTH), 422, "validation_error")
    o = c.get(f"/offers/{oid}", headers=AUTH).json()
    assert o["sources"][0]["vin"] == "WAUZZZF45HN038587" and o["market_pct"] == -12.5
    s = c.get("/second-source", headers=AUTH).json()
    assert s["matched"] == 1 and "side_runs" in s
    assert c.get("/market").status_code == 401
