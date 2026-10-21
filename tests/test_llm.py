"""LLM-функция на записанных ответах модели, без сети."""
import json
from pathlib import Path

import httpx

from kiravto.llm import FactExtractor, LLMClient, verify_facts
from tests.conftest import load_json

LLM_FIX = Path(__file__).parent / "fixtures" / "llm"


def recorded(name):
    return json.loads((LLM_FIX / name).read_text(encoding="utf-8"))


def make(conn, settings, clock, provider, response, calls):
    settings.llm_provider, settings.llm_api_key, settings.llm_model = provider, "test-key", "test-model"

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    client = LLMClient(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))
    return FactExtractor(conn, settings, client, clock=clock)


def test_quotes_verified_and_fakes_dropped(conn, settings, clock):
    desc = load_json("card_1160094460-61dcf15b.json")["description"]
    calls = []
    fx = make(conn, settings, clock, "openrouter", recorded("openai_card_1160094460.json"), calls)
    facts = fx.facts_for(desc)
    assert [f["quote"] for f in facts] == ["5 регистраций (фактически 2 вл)",
                                          "Обслуживание в профильном сервисе с применением качественных расходников"]
    u = fx.usage()
    # выброшены: «один владелец по ПТС» (нет в тексте) и цитата, где в оригинале латинские буквы-двойники
    assert u["facts_kept"] == 2 and u["facts_dropped"] == 2
    assert u["today"]["tokens_in"] == 1187 and u["total_cost_usd"] > 0
    assert calls[0]["model"] == "test-model"


def test_same_description_not_sent_twice(conn, settings, clock):
    desc = load_json("card_1160094460-61dcf15b.json")["description"]
    calls = []
    fx = make(conn, settings, clock, "openrouter", recorded("openai_card_1160094460.json"), calls)
    fx.facts_for(desc)
    fx.facts_for(desc)
    fx.facts_for("  " + desc + "\n")   # тот же текст с краевыми пробелами
    assert len(calls) == 1


def test_anthropic_format_and_hallucination_dropped(conn, settings, clock):
    desc = load_json("card_1160027054-72d39620.json")["description"]
    fx = make(conn, settings, clock, "anthropic", recorded("anthropic_card_1160027054.json"), [])
    facts = fx.facts_for(desc)
    assert facts == [{"fact": "Гарантия РОЛЬФ 2 года", "quote": "2 года гарантии РОЛЬФ"}]
    assert fx.usage()["facts_dropped"] == 1


def test_budget_exhausted_disables_function(conn, settings, clock):
    settings.llm_daily_budget_usd = 0.0
    calls = []
    fx = make(conn, settings, clock, "openrouter", recorded("openai_card_1160094460.json"), calls)
    assert fx.facts_for("Один владелец, обслуживание у официального дилера") is None
    assert calls == []


def test_no_key_service_works(conn, settings, clock, source):
    from kiravto.collector import Collector
    fx = FactExtractor(conn, settings, client=None)
    assert not fx.enabled
    assert fx.facts_for("какой-то текст") is None
    assert fx.process_pending()["disabled"] is True
    assert Collector(conn, source, settings, clock=clock).run()["status"] == "complete"


def test_model_error_does_not_break(conn, settings, clock):
    settings.llm_provider, settings.llm_api_key, settings.llm_model = "openrouter", "k", "m"
    client = LLMClient(settings, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert FactExtractor(conn, settings, client, clock=clock).facts_for("текст описания машины") is None


def test_verify_rules():
    desc = "Один владелец. Обслуживание у официального дилера, есть сервисная книжка."
    kept, dropped = verify_facts(desc, [
        {"fact": "1 владелец", "quote": "один   владелец"},           # пробелы/регистр — ок
        {"fact": "дилер", "quote": "обслуживание у дилера"},           # перефраз — выброс
        {"fact": "x", "quote": "есть"},                               # слишком короткая цитата
        "мусор",
    ])
    assert len(kept) == 1 and dropped == 3


def test_dropped_facts_visible_in_stats_and_report(conn, settings, clock):
    from kiravto import queries
    from kiravto.report import build_html
    desc = load_json("card_1160094460-61dcf15b.json")["description"]
    make(conn, settings, clock, "openrouter", recorded("openai_card_1160094460.json"), []).facts_for(desc)
    st = queries.stock_summary(conn)["llm"]
    assert (st["facts_kept"], st["facts_dropped"], st["calls"]) == (2, 2, 1)
    html = build_html(conn, settings=settings)
    assert "фактов выброшено" in html and "None" not in html
