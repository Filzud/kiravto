"""Голосовой вопрос в админ-чате: распознавание → инструмент MCP → ответ. Без сети: STT подменяется,
кроме одного теста на настоящей модели (запускается, только если модель уже скачана)."""
import json
import os
from pathlib import Path

import pytest

from kiravto import mcp_server, voice
from tests.test_telegram import (ADMIN, BAD, FOREIGN, bot, fake, make_bot, stocked, tg_settings)  # noqa: F401

VOICE_OGG = Path(__file__).parent / "fixtures" / "voice_question.ogg"


@pytest.fixture(autouse=True)
def mcp_db(settings, monkeypatch):
    # инструменты MCP открывают базу сами — направляем их в тестовую
    monkeypatch.setenv("DB_PATH", str(settings.db_path))
    monkeypatch.setattr(mcp_server, "_settings", None)


@pytest.mark.parametrize("question, tool, args", [
    ("Какие машины подешевели больше, чем на 5% за неделю?", "get_price_drops", {"days": 7, "min_pct": 5.0}),
    ("что подешевело за месяц больше чем на десять процентов", "get_price_drops", {"days": 30, "min_pct": 10}),
    ("Что снизилось в цене за сутки", "get_price_drops", {"days": 1, "min_pct": 0.1}),
    ("Какие машины ниже рынка?", "get_below_market", {"min_pct": 5.0}),
    ("Что выгоднее рынка на 10 процентов", "get_below_market", {"min_pct": 10}),
    ("Сколько машин на складе", "get_stock_summary", {}),
    ("Когда был последний прогон", "get_runs_status", {"limit": 3}),
    ("Найди новые тойоты до 6 млн", "search_offers", {"limit": 5, "mark": "TOYOTA", "section": "new",
                                                       "price_max": 6_000_000}),
    ("Есть ли мерседесы с пробегом", "search_offers", {"limit": 5, "mark": "MERCEDES", "section": "used"}),
])
def test_route(question, tool, args):
    assert voice.route(question) == (tool, args)


def test_unknown_question_without_llm():
    assert voice.route("расскажи анекдот") is None
    assert "Не понял" in voice.answer("расскажи анекдот")


class FakeLLM:
    def __init__(self, text):
        self.text = text

    def chat(self, system, user, json_mode=True):
        return self.text, 10, 5


def test_llm_route_whitelist():
    assert voice.route_llm("q", FakeLLM('{"tool": "get_stock_summary", "args": {}}')) == ("get_stock_summary", {})
    # чужой инструмент и лишние аргументы не проходят — публиковать голосом нельзя
    assert voice.route_llm("q", FakeLLM('{"tool": "propose_post", "args": {"offer_id": "1"}}')) is None
    assert voice.route_llm("q", FakeLLM('{"tool": "get_price_drops", "args": {"days": 7, "x": 1}}')) == \
        ("get_price_drops", {"days": 7})
    assert voice.route_llm("q", FakeLLM("не JSON")) is None


def test_voice_message_answered_from_mcp_tool(conn, bot, fake, stocked, monkeypatch):  # noqa: F811
    heard = []
    monkeypatch.setattr(voice, "transcribe", lambda audio, model, proxy="": heard.append(audio) or
                        "Какие машины подешевели больше чем на 5 процентов за неделю")
    fake.files["voice/v1.oga"] = b"OggS-fake-audio"
    fake.queue_update("message", {"message_id": 5, "chat": {"id": int(ADMIN)}, "from": {"id": 42},
                                  "voice": {"file_id": "v1", "duration": 4, "mime_type": "audio/ogg"}})
    bot.poll_once(timeout=0)
    assert heard == [b"OggS-fake-audio"]                       # скачали именно файл голосового
    texts = [p["text"] for p in fake.by("sendMessage")]
    assert len(texts) == 1 and all(p["chat_id"] == ADMIN for p in fake.by("sendMessage"))
    t = texts[0]
    assert "get_price_drops" in t and "Подешевели за 7 дн. от 5%: 1" in t
    victim = conn.execute("SELECT url FROM offers WHERE id=?", (stocked["victim_id"],)).fetchone()[0]
    assert victim in t and "−10.0%" in t
    for bad in ("None", "undefined"):
        assert bad not in t
    assert not [c for c in fake.calls if c[0] in ("sendPhoto", "sendMediaGroup")]   # голосом ничего не публикуется


def test_voice_from_foreign_chat_ignored(bot, fake, monkeypatch):  # noqa: F811
    monkeypatch.setattr(voice, "transcribe", lambda *a, **k: pytest.fail("чужой чат не распознаём"))
    fake.queue_update("message", {"message_id": 5, "chat": {"id": int(FOREIGN)}, "voice": {"file_id": "v1"}})
    bot.poll_once(timeout=0)
    assert fake.by("sendMessage") == [] and fake.by("getFile") == []


def test_ask_command_and_long_voice(conn, bot, fake, stocked):  # noqa: F811
    fake.command("/ask сколько машин на складе")
    bot.poll_once(timeout=0)
    t = fake.by("sendMessage")[-1]["text"]
    assert "get_stock_summary" in t and "В продаже" in t
    fake.reset()
    fake.queue_update("message", {"message_id": 6, "chat": {"id": int(ADMIN)}, "voice": {"file_id": "v2", "duration": 300}})
    bot.poll_once(timeout=0)
    assert "длиннее минуты" in fake.by("sendMessage")[-1]["text"]


def test_stt_failure_answers_politely(bot, fake, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("модель не скачалась")
    monkeypatch.setattr(voice, "transcribe", boom)
    fake.files["voice/v3.oga"] = b"x"
    fake.queue_update("message", {"message_id": 7, "chat": {"id": int(ADMIN)}, "voice": {"file_id": "v3", "duration": 3}})
    bot.poll_once(timeout=0)
    assert "Не удалось распознать" in fake.by("sendMessage")[-1]["text"]


def _model_cached(name: str) -> bool:
    try:
        from faster_whisper.utils import download_model
        download_model(name, local_files_only=True)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _model_cached("small"), reason="модель faster-whisper small не скачана (тест без сети)")
def test_real_stt_on_recorded_voice():
    """OGG/Opus, как голосовое Telegram (синтез речи Windows «Irina», 22.10.2026)."""
    text = voice.transcribe(VOICE_OGG.read_bytes(), "small")
    assert "подешевел" in text.lower() and "5" in text
    assert voice.route(text) == ("get_price_drops", {"days": 7, "min_pct": 5.0})
