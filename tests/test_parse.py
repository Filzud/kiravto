import copy

import pytest

from kiravto import normalize as nz
from kiravto.source.autoru import _decode
from kiravto.source.parse import (SourceBlocked, SourceFormatError, check_listing_response, parse_card,
                                  parse_offer)
from tests.conftest import load_json


def test_listing_envelope_and_offer_fields():
    data = check_listing_response(load_json("listing_used_p1.json"))
    offers = [parse_offer(o) for o in data["offers"]]
    assert len(offers) == 14
    o = offers[0]
    for field in ("id", "url", "mark", "model", "year", "mileage", "price", "price_discount", "engine_power",
                  "fuel", "transmission", "drive", "body", "color", "vin", "owners", "pts", "availability",
                  "created_at_source"):
        assert o[field] not in (None, ""), field
    assert o["url"].startswith("https://auto.ru/cars/used/sale/")
    assert o["url"].endswith(f"{o['id']}-{o['hash']}/")
    assert all(p.endswith("/orig") and p.startswith("https://") for p in o["photos"])


def test_new_cars_have_no_owner_fields():
    data = load_json("listing_new_p1.json")
    o = parse_offer(data["offers"][0])
    assert o["section"] == "new"
    assert o["owners"] is None and o["pts"] is None
    assert o["availability"] in ("IN_STOCK", "IN_TRANSIT", "ON_ORDER")


def test_card_hongqi_matches_site():
    # значения сверены руками со страницей объявления 20.10.2026
    c = parse_card(load_json("card_1160027054-72d39620.json"))
    assert c["mark"] == "Hongqi" and c["model"] == "HS5" and c["generation"] == "I"
    assert c["complectation"] == "Deluxe"
    assert c["year"] == 2023 and c["mileage"] == 28721
    assert c["price"] == 2_649_000
    assert c["price_discount"] == 2_419_000            # «2 419 000 ₽ со скидками»
    assert (c["discount_tradein"], c["discount_credit"], c["discount_insurance"]) == (40000, 150000, 40000)
    assert c["engine_volume"] == 2.0 and c["engine_power"] == 218 and c["fuel"] == "бензин"
    assert c["transmission"] == "автомат" and c["drive"] == "полный"
    assert c["color"] == "жёлтый"
    assert c["vin"] == "LFB**************" and c["vin_full"] is False
    assert c["owners"] == 1 and c["pts"] == "оригинал" and c["customs_cleared"] is True
    assert len(c["photos"]) == 25
    assert "РОЛЬФ" in c["description"]


def test_card_not_found_is_none():
    assert parse_card(load_json("card_1000000001-deadbeef.json")) is None


def test_format_change_raises_clear_error():
    raw = copy.deepcopy(load_json("listing_used_p1.json")["offers"][0])
    del raw["vehicle_info"]["tech_param"]
    with pytest.raises(SourceFormatError, match="tech_param"):
        parse_offer(raw)
    env = load_json("listing_used_p1.json")
    del env["pagination"]
    with pytest.raises(SourceFormatError, match="pagination"):
        check_listing_response(env)


def test_need_auth_is_block_not_format():
    with pytest.raises(SourceBlocked):
        check_listing_response({"action_kind": "NEED_AUTH", "error": "ACTION_REQUIRED", "status": "ERROR"})


def test_decode_captcha_html_and_redirect():
    with pytest.raises(SourceBlocked):
        _decode({"status": 200, "url": "https://auto.ru/showcaptcha?x", "text": "<html>"}, "t")
    with pytest.raises(SourceBlocked):
        _decode({"status": 200, "url": "https://auth.auto.ru/login/", "text": "{}"}, "t")


@pytest.mark.parametrize("text", ["Mercedes-Benz", "Мерседес", "Mercedes", "MB", "mercedes benz", "МЕРСЕДЕС"])
def test_mark_dictionary(text):
    assert nz.resolve_mark(text) == "MERCEDES"


def test_anomalies_marked_not_dropped():
    med = {("KIA", "RIO"): 1_500_000}
    o = {"price": 1, "section": "used", "mileage": 0, "year": 2031, "mark_code": "KIA", "model_code": "RIO"}
    an = nz.find_anomalies(o, med)
    assert {"price_too_low", "zero_mileage_used", "year_in_future", "price_10x_below_median"} <= set(an)


def test_removed_card_is_not_format_change():
    # реальный ответ 21.10.2026: в выдаче объявление ещё есть, карточка уже REMOVED и без цены
    c = parse_card(load_json("card_removed_1160201304.json"))
    assert c == {"id": "1160201304", "status_source": "REMOVED", "inactive": True}


def test_empty_body_is_transient_not_format_change():
    from kiravto.source.autoru import TransientError
    with pytest.raises(TransientError):
        _decode({"status": 200, "url": "https://auto.ru/-/ajax/desktop-search/getOfferCard/", "text": ""}, "t")


def test_source_retries_empty_then_succeeds(monkeypatch):
    """Пустой ответ → повтор с паузой → нормальная карточка; после всех неудач — блокировка, а не падение."""
    from kiravto.source import autoru
    monkeypatch.setattr(autoru.time, "sleep", lambda s: None)
    answers = [{"status": 200, "url": "u", "text": ""}, {"status": 200, "url": "u", "text": '{"ok": 1}'}]

    class Page:
        def evaluate(self, js, args):
            return answers.pop(0)

    src = autoru.AutoruBrowserSource("d", "1", pause=(0, 0))
    src._page = Page()
    assert src.card("1-a") == {"ok": 1}
    assert src.errors == 1 and src.requests == 2
    answers[:] = [{"status": 200, "url": "u", "text": ""}] * 3
    with pytest.raises(SourceBlocked):
        src.card("1-a")


def test_fetch_timeout_is_transient(monkeypatch):
    from kiravto.source import autoru
    monkeypatch.setattr(autoru.time, "sleep", lambda s: None)
    answers = [{"status": 0, "url": "u", "text": "", "error": "AbortError: signal is aborted"},
               {"status": 200, "url": "u", "text": '{"ok": 1}'}]

    class Page:
        def evaluate(self, js, args):
            assert args[2] == autoru.FETCH_TIMEOUT_MS
            return answers.pop(0)

    src = autoru.AutoruBrowserSource("d", "1", pause=(0, 0))
    src._page = Page()
    assert src.card("1-a") == {"ok": 1}


def test_engine_volume_as_site_shows_it():
    from kiravto.source.parse import _site_volume
    # Lexus LX 600d: 3346 см³, сайт пишет «3.4d» (сверщик нашёл расхождение 3.3 ≠ 3.4)
    assert _site_volume("3.4d AT (299 л.с.) 4WD", 3346) == 3.4
    assert _site_volume("6.8 AT (571 л.с.) 4WD", 6749) == 6.8
    assert _site_volume(None, 1989) == 2.0
    assert _site_volume("", None) is None
    c = parse_card(load_json("card_1160027054-72d39620.json"))
    assert c["engine_volume"] == 2.0 and c["engine_cc"] == 1989
