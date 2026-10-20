"""Бот модерации против настоящего HTTP-сервера, который притворяется Bot API (127.0.0.1, без сети)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kiravto import queries
from kiravto.collector import Collector
from kiravto.db import connect, kv_get, row_to_offer
from kiravto.posts import daily_new_post, drop_post, fmt_rub, queue_post
from kiravto.telegram_bot import BotAPI, ModerationBot, TelegramError, TelegramRetryAfter

ADMIN = "-100111"
TEST_CH = "-100222"
PROD_CH = "-100999"
FOREIGN = "-100555"
TOKEN = "TEST"


class FakeBotAPI:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.paths: list[str] = []
        self.failures: dict[str, list[tuple[int, dict]]] = {}
        self.updates: list[dict] = []
        self.next_update_id = 1000
        self.next_message_id = 1
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                params = json.loads(body or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                status, payload = fake.respond(self.path, method, params)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    # --- сценарии ---
    def fail(self, method: str, times: int, status: int = 500, retry_after: int | None = None):
        if status == 429:
            body = {"ok": False, "error_code": 429, "description": f"Too Many Requests: retry after {retry_after}",
                    "parameters": {"retry_after": retry_after}}
        else:
            body = {"ok": False, "error_code": status, "description": "Internal Server Error"}
        self.failures.setdefault(method, []).extend([(status, body)] * times)

    def queue_update(self, kind: str, obj: dict) -> int:
        uid = self.next_update_id
        self.next_update_id += 1
        self.updates.append({"update_id": uid, kind: obj})
        return uid

    def click(self, post_id: int, action: str = "pub", chat: str = ADMIN, username: str = "manager"):
        row_msg = {"message_id": 1, "chat": {"id": int(chat), "type": "supergroup"}}
        return self.queue_update("callback_query", {"id": f"cb{self.next_update_id}", "data": f"{action}:{post_id}",
                                                    "from": {"id": 42, "username": username}, "message": row_msg})

    def command(self, text: str, chat: str = ADMIN):
        return self.queue_update("message", {"message_id": 77, "text": text, "chat": {"id": int(chat)},
                                             "from": {"id": 42, "username": "manager"}})

    # --- ответы ---
    def respond(self, path, method, params):
        with self.lock:
            self.paths.append(path)
            self.calls.append((method, params))
            queue = self.failures.get(method)
            if queue:
                return queue.pop(0)
            if method == "getUpdates":
                offset = params.get("offset") or 0
                self.updates = [u for u in self.updates if u["update_id"] >= offset]  # подтверждённые забываем
                return 200, {"ok": True, "result": list(self.updates)}
            if method in ("answerCallbackQuery", "editMessageReplyMarkup"):
                return 200, {"ok": True, "result": True}
            if method == "sendMediaGroup":
                res = []
                for _ in params["media"]:
                    res.append({"message_id": self.next_message_id, "chat": {"id": params["chat_id"]}})
                    self.next_message_id += 1
                return 200, {"ok": True, "result": res}
            mid = self.next_message_id
            self.next_message_id += 1
            return 200, {"ok": True, "result": {"message_id": mid, "chat": {"id": params.get("chat_id")},
                                                "text": params.get("text")}}

    # --- выборки ---
    def by(self, *methods):
        return [p for m, p in self.calls if m in methods]

    def to_chat(self, chat):
        return [(m, p) for m, p in self.calls if str(p.get("chat_id")) == chat]

    def reset(self):
        self.calls.clear()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    f = FakeBotAPI()
    yield f
    f.close()


@pytest.fixture
def tg_settings(settings, fake):
    settings.tg_token = TOKEN
    settings.tg_api_base = fake.url
    settings.tg_admin_chat_id = ADMIN
    settings.tg_test_channel_id = TEST_CH
    settings.tg_prod_channel_id = PROD_CH
    settings.tg_publish_target = "test"
    return settings


def make_bot(conn, settings, clock):
    return ModerationBot(conn, settings, BotAPI(settings.tg_api_base, settings.tg_token), clock=clock)


@pytest.fixture
def bot(conn, tg_settings, clock):
    b = make_bot(conn, tg_settings, clock)
    yield b
    b.api.close()


@pytest.fixture
def stocked(conn, source, settings, clock):
    """Склад из сохранённых ответов: два прогона, у одной машины цена упала на 10%."""
    Collector(conn, source, settings, clock=clock).run()
    victim = source.offers["used"][3]
    old = victim["price_info"]["price"]
    victim["price_info"]["price"] = int(old * 0.9)
    clock.advance(minutes=45)
    Collector(conn, source, settings, clock=clock).run()
    return {"victim_id": str(victim["id"]), "old": old, "new": int(old * 0.9)}


def offers(conn):
    return [row_to_offer(r) for r in conn.execute("SELECT * FROM offers ORDER BY id")]


def drop_post_for(conn, stocked, clock, source="service"):
    o = next(x for x in offers(conn) if x["id"] == stocked["victim_id"])
    return queue_post(conn, "price_drop", f"drop:{o['id']}:{source}", drop_post(o, stocked["old"], stocked["new"]),
                      [o["id"]], source=source, now=clock())


def text_post(conn, clock, key="t1", text="Тестовый пост"):
    return queue_post(conn, "manual", key, {"text": text, "photos": []}, [], now=clock())


def status(conn, pid):
    return conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()


def channel_calls(fake):
    return [(m, p) for m, p in fake.calls if str(p.get("chat_id")) in (TEST_CH, PROD_CH)]


# (a)
def test_post_goes_to_admin_with_buttons_not_to_channel(conn, bot, fake, stocked, clock):
    pid = drop_post_for(conn, stocked, clock)
    assert bot.push_to_moderation() == 1
    bot.deliver()
    sent = fake.by("sendMessage")
    assert len(sent) == 1 and sent[0]["chat_id"] == ADMIN and sent[0]["parse_mode"] == "HTML"
    buttons = sent[0]["reply_markup"]["inline_keyboard"][0]
    assert [(b["text"], b["callback_data"]) for b in buttons] == [("Опубликовать", f"pub:{pid}"),
                                                                    ("Отклонить", f"rej:{pid}")]
    assert "Фото: 1" in sent[0]["text"] and "Цена снижена" in sent[0]["text"]
    assert channel_calls(fake) == []
    row = status(conn, pid)
    assert row["status"] == "moderation" and row["admin_message_id"] == 1
    assert bot.push_to_moderation() == 0          # повторно на модерацию не шлём


def test_mcp_post_marked(conn, bot, fake, stocked, clock):
    drop_post_for(conn, stocked, clock, source="mcp")
    bot.push_to_moderation()
    assert "предложено ИИ-агентом" in fake.by("sendMessage")[0]["text"]


# (b)
def test_publish_click_sends_exactly_once(conn, bot, fake, stocked, clock):
    pid = drop_post_for(conn, stocked, clock)
    bot.push_to_moderation()
    fake.click(pid)
    fake.click(pid)                                 # двойной клик
    assert bot.poll_once(timeout=0) == 2
    answers = [a["text"] for a in fake.by("answerCallbackQuery")]
    assert answers[1] == "Уже обработано"
    edits = fake.by("editMessageText")
    assert len(edits) == 1 and "@manager" in edits[0]["text"] and "reply_markup" not in edits[0]
    row = status(conn, pid)
    assert row["status"] == "approved" and row["decided_by"] == "@manager"

    assert bot.deliver() == 1
    fake.click(pid)                                 # клик уже после публикации
    bot.poll_once(timeout=0)
    assert bot.deliver() == 0
    pubs = channel_calls(fake)
    assert len(pubs) == 1 and pubs[0][0] == "sendPhoto" and pubs[0][1]["chat_id"] == TEST_CH
    assert stocked["victim_id"] in pubs[0][1]["caption"]
    row = status(conn, pid)
    assert row["status"] == "sent" and row["target_chat"] == TEST_CH and row["sent_at"]


# (c)
def test_reject_is_never_published(conn, bot, fake, stocked, clock):
    pid = drop_post_for(conn, stocked, clock)
    bot.push_to_moderation()
    fake.click(pid, "rej")
    fake.click(pid, "pub")                          # передумал — поздно
    bot.poll_once(timeout=0)
    assert status(conn, pid)["status"] == "rejected"
    assert [a["text"] for a in fake.by("answerCallbackQuery")] == ["Отклонено", "Уже обработано"]
    for _ in range(3):
        bot.tick()
        clock.advance(hours=1)
    assert channel_calls(fake) == []


def approve(bot, fake, pid):
    bot.push_to_moderation()
    fake.click(pid)
    bot.poll_once(timeout=0)
    assert status(bot.conn, pid)["status"] == "approved"


# (d)
def test_retry_after_keeps_post_and_sends_later(conn, bot, fake, stocked, clock):
    pid = drop_post_for(conn, stocked, clock)
    approve(bot, fake, pid)
    fake.fail("sendPhoto", 1, status=429, retry_after=30)
    assert bot.deliver() == 0
    row = status(conn, pid)
    assert row["status"] == "approved" and row["attempts"] == 1 and "retry after" in row["last_error"]
    assert pid in [i["id"] for i in queries.post_queue(conn)]
    clock.advance(seconds=20)
    assert bot.deliver() == 0                        # ещё рано: запроса даже не было
    assert len(fake.by("sendPhoto")) == 1
    clock.advance(seconds=11)
    assert bot.deliver() == 1
    assert len([c for c in channel_calls(fake)]) == 2 and status(conn, pid)["status"] == "sent"


# (e)
def test_restart_does_not_resend_or_rehandle(conn, tg_settings, fake, stocked, clock):
    bot1 = make_bot(conn, tg_settings, clock)
    pid = drop_post_for(conn, stocked, clock)
    other = text_post(conn, clock)
    bot1.push_to_moderation()
    fake.click(pid)
    fake.click(other)
    last = fake.command("/queue")
    bot1.poll_once(timeout=0)
    # имитация падения: второй пост бот «захватил» на отправку и умер, не дождавшись ответа
    conn.execute("UPDATE posts SET status='sending' WHERE id=?", (other,))
    conn.commit()
    assert bot1.deliver() == 1
    assert status(conn, pid)["status"] == "sent"
    assert kv_get(conn, "tg_update_offset") == str(last + 1)
    bot1.api.close()

    conn2 = connect(tg_settings.db_path)          # «перезапуск»: новое соединение и новый бот
    bot2 = make_bot(conn2, tg_settings, clock)
    fake.reset()
    assert bot2.poll_once(timeout=0) == 0
    clock.advance(hours=2)
    bot2.tick()
    assert [m for m, _ in fake.calls] == ["getUpdates"]
    assert fake.calls[0][1]["offset"] == last + 1
    assert status(conn2, other)["status"] == "sending"
    q = bot2.queue_text()
    assert f"#{other}" in q and "требует проверки человеком" in q
    bot2.api.close()
    conn2.close()


# (f)
def test_foreign_chat_ignored(conn, bot, fake, stocked, clock):
    pid = drop_post_for(conn, stocked, clock)
    bot.push_to_moderation()
    fake.reset()
    fake.click(pid, chat=FOREIGN)
    fake.click(pid, "rej", chat=FOREIGN)
    for cmd in ("/status", "/drops", "/queue", "/start"):
        fake.command(cmd, chat=FOREIGN)
    bot.handle_update({"update_id": 1, "message": {"text": "/status"}})          # чат не указан вовсе
    assert bot.poll_once(timeout=0) == 6
    assert [m for m, _ in fake.calls] == ["getUpdates"]
    assert status(conn, pid)["status"] == "moderation"


BAD = ("None", "undefined", "null", "{", "}")


def command_replies(bot, fake, cmd):
    fake.reset()
    fake.command(cmd)
    bot.poll_once(timeout=0)
    texts = [p["text"] for p in fake.by("sendMessage")]
    assert texts and all(p["chat_id"] == ADMIN for p in fake.by("sendMessage"))
    for t in texts:
        assert len(t) <= 4096
        for bad in BAD:
            assert bad not in t, (cmd, bad, t)
    return "\n".join(texts)


# (g)
def test_commands_empty_db(bot, fake):
    assert "не было" in command_replies(bot, fake, "/status")
    assert "не было" in command_replies(bot, fake, "/drops")
    assert "пуста" in command_replies(bot, fake, "/queue")


def test_commands_with_data(conn, bot, fake, stocked, clock, source, settings):
    pid = drop_post_for(conn, stocked, clock, source="mcp")
    text_post(conn, clock)
    approve(bot, fake, pid)
    fake.fail("sendPhoto", 1, status=500)
    bot.deliver()
    # неполный прогон с примечанием и неизвестным source_total
    conn.execute("INSERT INTO runs(started_at, status, collected, note) VALUES(?, 'incomplete', 3, ?)",
                 ("2026-10-20T10:00:00+00:00", "капча на странице 2"))
    conn.commit()

    st = command_replies(bot, fake, "/status")
    assert "20.10.2026 13:00" in st and "неполный" in st and "полный" in st and "собрано 22 из 22" in st
    assert "заявлено источником: неизвестно" in st and "капча" in st

    dr = command_replies(bot, fake, "/drops")
    assert "было" in dr and "→ стало" in dr and "−10.0%" in dr
    assert fmt_rub(stocked["old"]) in dr and fmt_rub(stocked["new"]) in dr

    q = command_replies(bot, fake, "/queue")
    assert f"#{pid}" in q and "одобрен" in q and "попыток 1" in q and "предложено ИИ-агентом" in q
    assert "на модерации" in q


def test_status_long_output_respects_limit(conn, bot, fake):
    for i in range(5):
        conn.execute("INSERT INTO runs(started_at, status, note) VALUES(?, 'failed', ?)",
                     (f"2026-10-20T0{i}:00:00+00:00", "x" * 3000))
    conn.commit()
    command_replies(bot, fake, "/status")


# (h)
def test_daily_post_album(conn, bot, fake, source, settings, clock):
    Collector(conn, source, settings, clock=clock).run()
    items = [o for o in offers(conn) if o["photos"]][:15]
    assert len(items) == 15
    payload = daily_new_post(items, "20.10.2026")
    pid = queue_post(conn, "daily_new", "daily:x", payload, [o["id"] for o in items], now=clock())
    # и пост «от агента» с 15 фото и слишком длинной подписью — бот сам режет до лимитов Telegram
    big = {"caption": "<b>Подпись</b>\n" + "\n".join(f"строка {i} " + "я" * 40 for i in range(60)),
           "text": "Полный список", "photos": [o["photos"][0] for o in items]}
    pid2 = queue_post(conn, "manual", "big", big, [], source="mcp", now=clock())
    bot.push_to_moderation()
    assert "Фото: 15" in fake.by("sendMessage")[1]["text"]
    fake.click(pid)
    fake.click(pid2)
    bot.poll_once(timeout=0)
    fake.reset()
    assert bot.deliver() == 2
    albums = fake.by("sendMediaGroup")
    assert len(albums) == 2
    for alb in albums:
        media = alb["media"]
        assert len(media) == 10 and alb["chat_id"] == TEST_CH
        assert len(media[0]["caption"]) <= 1024 and media[0]["parse_mode"] == "HTML"
        assert all("caption" not in m for m in media[1:])
    texts = fake.by("sendMessage")
    assert len(texts) == 2 and all(len(t["text"]) <= 4096 for t in texts)
    assert "Новые за день" in texts[0]["text"]
    order = [m for m, _ in fake.calls]
    assert order == ["sendMediaGroup", "sendMessage", "sendMediaGroup", "sendMessage"]


def test_album_not_repeated_if_text_part_failed(conn, bot, fake, source, settings, clock):
    Collector(conn, source, settings, clock=clock).run()
    items = [o for o in offers(conn) if o["photos"]][:5]
    pid = queue_post(conn, "daily_new", "daily:y", daily_new_post(items, "20.10.2026"), [], now=clock())
    approve(bot, fake, pid)
    fake.reset()
    fake.fail("sendMessage", 1, status=500)
    assert bot.deliver() == 0
    clock.advance(minutes=5)
    assert bot.deliver() == 1
    assert len(fake.by("sendMediaGroup")) == 1 and len(fake.by("sendMessage")) == 2


def test_long_text_split(conn, bot, fake, clock):
    pid = text_post(conn, clock, text="\n".join("строка " + "ж" * 90 for _ in range(100)))
    approve(bot, fake, pid)
    fake.reset()
    bot.deliver()
    parts = fake.by("sendMessage")
    assert len(parts) == 3 and all(len(p["text"]) <= 4096 for p in parts)


# (i)
def test_prod_channel_only_by_human_setting(conn, bot, fake, clock):
    p1 = text_post(conn, clock, "p1")
    approve(bot, fake, p1)
    bot.deliver()
    assert all(str(p.get("chat_id")) != PROD_CH for _, p in fake.calls)
    assert status(conn, p1)["target_chat"] == TEST_CH

    bot.settings.tg_publish_target = "prod"         # как будто человек поправил .env
    p2 = text_post(conn, clock, "p2")
    approve(bot, fake, p2)
    fake.reset()
    bot.deliver()
    assert [p["chat_id"] for _, p in channel_calls(fake)] == [PROD_CH]


def test_bot_code_has_no_other_channel_switch():
    src = (Path(__file__).parent.parent / "kiravto" / "telegram_bot.py").read_text(encoding="utf-8")
    assert "tg_prod_channel_id" not in src and "tg_publish_target" not in src
    assert "tg_test_channel_id" not in src


# (j)
def test_server_errors_backoff_then_failed(conn, bot, fake, clock):
    pid = text_post(conn, clock)
    approve(bot, fake, pid)
    fake.reset()
    fake.fail("sendMessage", 100, status=500)
    delays = []
    for _ in range(8):
        now = clock()
        assert bot.deliver() == 0
        row = status(conn, pid)
        if row["status"] == "approved":
            from kiravto.db import parse_iso
            delays.append((parse_iso(row["next_attempt_at"]) - now).total_seconds())
            assert bot.deliver() == 0               # до срока повтора не трогаем
        clock.advance(hours=1, seconds=1)
    assert delays == [60, 120, 240, 480, 960, 1920, 3600]
    row = status(conn, pid)
    assert row["status"] == "failed" and row["attempts"] == 8 and "500" in row["last_error"]
    assert len(fake.by("sendMessage")) == 8
    q = [i for i in queries.post_queue(conn) if i["id"] == pid]
    assert q and q[0]["status"] == "failed"
    assert "не отправлен" in bot.queue_text()
    clock.advance(days=1)
    bot.deliver()
    assert len(fake.by("sendMessage")) == 8


def test_network_error_is_retried(conn, tg_settings, clock):
    tg_settings.tg_api_base = "http://127.0.0.1:1"      # никто не слушает
    b = make_bot(conn, tg_settings, clock)
    pid = text_post(conn, clock)
    conn.execute("UPDATE posts SET status='approved' WHERE id=?", (pid,))
    conn.commit()
    assert b.deliver() == 0
    row = status(conn, pid)
    assert row["status"] == "approved" and row["attempts"] == 1 and "сетевая ошибка" in row["last_error"]
    assert "/botTEST" not in row["last_error"] and "127.0.0.1" not in row["last_error"]
    assert b.send_admin("два неполных прогона подряд") is False
    b.api.close()


def test_api_errors_and_token_privacy(fake):
    api = BotAPI(fake.url, "secret-tok")
    fake.fail("sendMessage", 1, status=429, retry_after=7)
    with pytest.raises(TelegramRetryAfter) as e:
        api.call("sendMessage", chat_id=ADMIN, text="x")
    assert e.value.retry_after == 7 and "secret-tok" not in str(e.value)
    fake.fail("sendMessage", 1, status=400)
    with pytest.raises(TelegramError) as e2:
        api.call("sendMessage", chat_id=ADMIN, text="x")
    assert "secret-tok" not in str(e2.value) and "secret-tok" not in repr(api)
    assert fake.paths[-1] == "/botsecret-tok/sendMessage"
    api.close()


def test_send_admin(bot, fake):
    assert bot.send_admin("⚠️ два неполных прогона подряд") is True
    assert fake.by("sendMessage")[0]["chat_id"] == ADMIN
    fake.fail("sendMessage", 1, status=500)
    assert bot.send_admin("ещё") is False


def test_run_forever_stops(conn, bot, fake, clock):
    pid = text_post(conn, clock)
    stop = threading.Event()
    orig = bot.tick

    def tick_then_stop():
        orig()
        stop.set()

    bot.tick = tick_then_stop
    fake.fail("getUpdates", 1, status=500)          # первая итерация падает — цикл живёт дальше
    t = threading.Thread(target=bot.run_forever, kwargs={"stop_event": stop, "poll_timeout": 0, "error_sleep": 0})
    t.start()
    t.join(10)
    assert not t.is_alive()
    assert status(conn, pid)["status"] == "moderation"
