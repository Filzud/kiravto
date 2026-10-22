"""Telegram: модерация постов и доставка в канал.

Порядок жизни поста: new → moderation (ушёл в админ-чат с кнопками) → approved / rejected (кнопка человека)
→ sending → sent | failed. Публикует бот только 'approved', и только в settings.publish_channel_id.
Пост, застрявший в 'sending' после падения, сам не переотправляется — его проверяет человек (/queue).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from . import db as kdb
from . import queries
from .config import Settings
from .posts import ALBUM_LIMIT, CAPTION_LIMIT, TEXT_LIMIT, fmt_rub

log = logging.getLogger("kiravto.telegram")
# httpx на INFO пишет полный URL запроса, а в нём токен бота — глушим
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

MAX_ATTEMPTS = 8
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
OFFSET_KEY = "tg_update_offset"

KIND_RU = {"daily_new": "Новые за день", "price_drop": "Цена снижена", "manual": "Ручной пост"}
POST_STATUS_RU = {"new": "ждёт отправки на модерацию", "moderation": "на модерации",
                  "approved": "одобрен, ждёт отправки", "sending": "требует проверки человеком",
                  "failed": "не отправлен (ошибки)", "rejected": "отклонён", "sent": "опубликован"}
RUN_STATUS_RU = {"complete": "полный", "incomplete": "неполный", "failed": "ошибка", "running": "идёт"}


class TelegramError(Exception):
    pass


class TelegramRetryAfter(TelegramError):
    def __init__(self, retry_after: int, description: str = ""):
        super().__init__(description or f"Too Many Requests: retry after {retry_after}")
        self.retry_after = int(retry_after)


class BotAPI:
    """Тонкая обёртка над Bot API. Токен нигде не печатается и в исключения не попадает."""

    def __init__(self, base_url: str, token: str, proxy: str = "", client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._token = token
        if client is None:
            kw = {"trust_env": False, "timeout": httpx.Timeout(30.0, connect=10.0)}
            if proxy:
                kw["proxy"] = proxy
            client = httpx.Client(**kw)
        self._client = client

    def __repr__(self) -> str:
        return f"BotAPI(base_url={self._base!r})"

    def _redact(self, s: str) -> str:
        return s.replace(self._token, "***") if self._token else s

    def call(self, method: str, files: dict[str, tuple[str, bytes]] | None = None, **params):
        params = {k: v for k, v in params.items() if v is not None}
        timeout = None
        if method == "getUpdates":
            timeout = httpx.Timeout(float(params.get("timeout", 0)) + 15.0, connect=10.0)
        if files:
            # multipart: вложенные поля — JSON-строкой, файлы — отдельными частями (attach://имя)
            body = {"data": {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in params.items()},
                    "files": {name: (fn, data, "image/jpeg") for name, (fn, data) in files.items()},
                    "timeout": httpx.Timeout(90.0, connect=10.0)}
        else:
            body = {"json": params, **({"timeout": timeout} if timeout else {})}
        try:
            resp = self._client.post(f"{self._base}/bot{self._token}/{method}", **body)
        except httpx.HTTPError as e:
            raise TelegramError(f"{method}: сетевая ошибка {type(e).__name__}: {self._redact(str(e))}") from None
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {resp.status_code}, ответ не JSON") from None
        if not isinstance(data, dict) or not data.get("ok"):
            data = data if isinstance(data, dict) else {}
            desc = self._redact(str(data.get("description") or f"HTTP {resp.status_code}"))
            code = data.get("error_code") or resp.status_code
            if code == 429:
                retry = (data.get("parameters") or {}).get("retry_after") or 30
                raise TelegramRetryAfter(int(retry), f"{method}: {desc}")
            raise TelegramError(f"{method}: {code} {desc}")
        return data.get("result")

    def download(self, file_path: str) -> bytes:
        """Файл из Telegram (голосовое) по file_path из getFile."""
        try:
            resp = self._client.get(f"{self._base}/file/bot{self._token}/{file_path}",
                                    timeout=httpx.Timeout(60.0, connect=10.0))
        except httpx.HTTPError as e:
            raise TelegramError(f"download: сетевая ошибка {type(e).__name__}: {self._redact(str(e))}") from None
        if resp.status_code != 200:
            raise TelegramError(f"download: HTTP {resp.status_code}")
        return resp.content

    def close(self) -> None:
        self._client.close()


# ---------- форматирование ----------

def _tz(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # на Windows без пакета tzdata базы зон нет; Москва — UTC+3 без переходов с 2014 года
        return timezone(timedelta(hours=3), "MSK")


def fmt_local(ts: str | None, tz_name: str) -> str:
    if not ts:
        return "—"
    try:
        dt = kdb.parse_iso(ts)
    except ValueError:
        return escape(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz(tz_name)).strftime("%d.%m.%Y %H:%M")


def _n(v) -> str:
    return "—" if v is None else str(v)


def trim_lines(text: str, limit: int) -> str:
    """Обрезка по границе строки, чтобы не разрезать HTML-тег посередине."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if "\n" in cut:
        return cut[: cut.rfind("\n")] + "\n…"
    return cut + "…"


def split_text(text: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Режет длинный текст на сообщения по строкам (строку длиннее лимита — по символам)."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _who(user: dict | None) -> str:
    user = user or {}
    if user.get("username"):
        return "@" + str(user["username"])
    name = " ".join(str(p) for p in (user.get("first_name"), user.get("last_name")) if p)
    return name or f"id {user.get('id', '?')}"


def _title(o: dict) -> str:
    return " ".join(str(p) for p in (o.get("mark"), o.get("model")) if p) or f"объявление {o.get('id')}"


class ModerationBot:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, api: BotAPI, clock=kdb.utcnow,
                 photo_fetch=None):
        self.conn = conn
        self.settings = settings
        self.api = api
        self.clock = clock
        # Серверы Telegram не могут скачать фото с CDN Авто.ру по ссылке («failed to get HTTP URL content»,
        # живой тест 21.10.2026). С photo_fetch сервис скачивает фото сам и отправляет файлом.
        self.photo_fetch = photo_fetch

    # ---------- помощники ----------

    @property
    def admin_chat(self) -> str:
        return str(self.settings.tg_admin_chat_id or "")

    def _now(self) -> datetime:
        return self.clock()

    def _post(self, post_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()

    def _preview(self, row: sqlite3.Row) -> str:
        payload = json.loads(row["payload"])
        photos = payload.get("photos") or []
        head = [f"📝 <b>Пост #{row['id']}</b> · {KIND_RU.get(row['kind'], escape(str(row['kind'])))}"]
        if row["source"] == "mcp":
            head.append("🤖 <i>предложено ИИ-агентом</i>")
            if payload.get("moderator_note"):
                head.append("Комментарий агента: " + escape(str(payload["moderator_note"])))
        if len(photos) > 1:
            head.append(f"📷 Фото: {len(photos)} (в канал уйдёт альбом из {min(len(photos), ALBUM_LIMIT)})")
        elif photos:
            head.append("📷 Фото: 1")
        else:
            head.append("📷 Без фото")
        head = "\n".join(head) + "\n\n"
        body = payload.get("text") or payload.get("caption") or "(пустой текст)"
        return head + trim_lines(body, TEXT_LIMIT - len(head) - 120)

    def _keyboard(self, post_id: int) -> dict:
        return {"inline_keyboard": [[{"text": "Опубликовать", "callback_data": f"pub:{post_id}"},
                                     {"text": "Отклонить", "callback_data": f"rej:{post_id}"}]]}

    def _reply(self, text: str) -> None:
        for chunk in split_text(text):
            self.api.call("sendMessage", chat_id=self.admin_chat, text=chunk, parse_mode="HTML",
                          link_preview_options={"is_disabled": True})

    # ---------- модерация ----------

    def push_to_moderation(self) -> int:
        if not self.admin_chat:
            log.warning("TG_ADMIN_CHAT_ID не задан — посты на модерацию не отправляются")
            return 0
        rows = self.conn.execute("SELECT * FROM posts WHERE status='new' ORDER BY id").fetchall()
        pushed = 0
        for row in rows:
            try:
                msg = self.api.call("sendMessage", chat_id=self.admin_chat, text=self._preview(row),
                                    parse_mode="HTML", reply_markup=self._keyboard(row["id"]),
                                    link_preview_options={"is_disabled": True})
            except TelegramRetryAfter as e:
                log.warning("модерация: лимит Telegram, пауза %s с", e.retry_after)
                break
            except TelegramError as e:
                log.error("модерация: пост #%s не отправлен: %s", row["id"], e)
                continue
            self.conn.execute("UPDATE posts SET status='moderation', admin_message_id=? WHERE id=? AND status='new'",
                              ((msg or {}).get("message_id"), row["id"]))
            self.conn.commit()
            pushed += 1
        return pushed

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            cq = update["callback_query"] or {}
            chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
            if not self.admin_chat or str(chat_id) != self.admin_chat:
                return  # чужой чат — молчим
            self._handle_callback(cq)
        elif "message" in update:
            msg = update["message"] or {}
            chat_id = (msg.get("chat") or {}).get("id")
            if msg.get("migrate_to_chat_id") and str(chat_id) == self.admin_chat:
                # Telegram превратил группу в супергруппу — у неё новый ID, старый перестаёт работать
                log.error("админ-группа стала супергруппой: новый ID %s — впишите его в TG_ADMIN_CHAT_ID "
                          "(python -m kiravto secrets)", msg["migrate_to_chat_id"])
                return
            if not self.admin_chat or str(chat_id) != self.admin_chat:
                if (msg.get("text") or "").startswith("/"):
                    log.info("команда из чужого чата %s проигнорирована (админ-чат: %s)", chat_id, self.admin_chat)
                return
            if msg.get("voice") or msg.get("audio"):
                self._reply(self.voice_text(msg.get("voice") or msg.get("audio")))
                return
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                return
            cmd = text.split()[0].split("@")[0].lower()
            if cmd == "/ask":
                question = text.partition(" ")[2].strip()
                self._reply(self.ask_text(question) if question else "Напишите вопрос после /ask или пришлите голосовое.")
                return
            handler = {"/status": self.status_text, "/drops": self.drops_text, "/queue": self.queue_text}.get(cmd)
            if handler is None:
                if cmd in ("/start", "/help"):
                    self._reply("Команды: /status — последние прогоны, /drops — снижения цен за 7 дней, "
                                "/queue — очередь постов, /ask вопрос — вопрос о складе. "
                                "Можно спросить голосом: «что подешевело за неделю больше чем на 5 процентов».")
                return
            self._reply(handler())

    def _handle_callback(self, cq: dict) -> None:
        data = str(cq.get("data") or "")
        action, _, sid = data.partition(":")
        if action not in ("pub", "rej") or not sid.isdigit():
            self.api.call("answerCallbackQuery", callback_query_id=cq.get("id"), text="Неизвестная кнопка")
            return
        post_id = int(sid)
        who = _who(cq.get("from"))
        new_status = "approved" if action == "pub" else "rejected"
        cur = self.conn.execute(
            "UPDATE posts SET status=?, decided_at=?, decided_by=? WHERE id=? AND status='moderation'",
            (new_status, kdb.iso(self._now()), who, post_id))
        self.conn.commit()
        if cur.rowcount == 0:
            self.api.call("answerCallbackQuery", callback_query_id=cq.get("id"), text="Уже обработано")
            return
        verdict = f"✅ Одобрил {escape(who)} — уйдёт в канал" if action == "pub" else f"❌ Отклонил {escape(who)}"
        self.api.call("answerCallbackQuery", callback_query_id=cq.get("id"),
                      text="Одобрено, пост уйдёт в канал" if action == "pub" else "Отклонено")
        msg = cq.get("message") or {}
        row = self._post(post_id)
        try:
            self.api.call("editMessageText", chat_id=self.admin_chat, message_id=msg.get("message_id"),
                          text=trim_lines(self._preview(row), TEXT_LIMIT - 100) + "\n\n" + verdict,
                          parse_mode="HTML", link_preview_options={"is_disabled": True})
        except TelegramError as e:
            log.warning("не удалось обновить сообщение модерации #%s: %s", post_id, e)
            try:
                self.api.call("editMessageReplyMarkup", chat_id=self.admin_chat, message_id=msg.get("message_id"),
                              reply_markup={"inline_keyboard": []})
            except TelegramError as e2:
                log.warning("не удалось убрать кнопки #%s: %s", post_id, e2)

    # ---------- команды ----------

    def status_text(self) -> str:
        runs = queries.last_runs(self.conn, 5)
        if not runs:
            return "Прогонов пока не было."
        tz = self.settings.timezone
        lines = ["<b>Последние прогоны</b>"]
        for r in runs:
            status = RUN_STATUS_RU.get(r.get("status") or "", escape(str(r.get("status") or "неизвестно")))
            total = r.get("source_total")
            collected = f"собрано {_n(r.get('collected'))}" + (f" из {total} заявленных" if total is not None
                                                                else ", заявлено источником: неизвестно")
            lines.append(
                f"\n<b>{fmt_local(r.get('started_at'), tz)}</b> — {status}\n"
                f"{collected}; запросов {_n(r.get('requests'))}, ошибок {_n(r.get('errors'))}\n"
                f"новых {_n(r.get('new_count'))}, изменений {_n(r.get('changed_count'))}, "
                f"снято {_n(r.get('removed_count'))}")
            if r.get("blocked"):
                lines.append("⚠️ упёрлись в защиту источника")
            if r.get("note"):
                lines.append("примечание: " + escape(trim_lines(str(r["note"]), 300)))
        return trim_lines("\n".join(lines), TEXT_LIMIT)

    def drops_text(self) -> str:
        drops = queries.price_drops(self.conn, days=7, min_pct=self.settings.drop_min_pct, now=self._now())
        if not drops:
            return f"За 7 дней снижений цены от {self.settings.drop_min_pct:g}% не было."
        lines = [f"<b>Снижения цены за 7 дней</b> (от {self.settings.drop_min_pct:g}%)"]
        for d in drops[:30]:
            year = f", {d['year']}" if d.get("year") else ""
            lines.append(f"\n<b>{escape(_title(d))}</b>{year}\n"
                         f"было {fmt_rub(d.get('old_price'))} → стало {fmt_rub(d.get('new_price'))} "
                         f"(−{d.get('drop_pct', 0):.1f}%)\n{escape(str(d.get('url') or ''))}")
        if len(drops) > 30:
            lines.append(f"\n…и ещё {len(drops) - 30}")
        return trim_lines("\n".join(lines), TEXT_LIMIT)

    def ask_text(self, question: str) -> str:
        from . import voice

        llm = None
        if self.settings.llm_enabled:
            from .llm import LLMClient
            llm = LLMClient(self.settings)
        try:
            return trim_lines(voice.answer(question, llm), TEXT_LIMIT)
        except Exception as e:  # noqa: BLE001 — вопрос не должен ронять бота
            log.exception("вопрос о складе")
            return f"Не получилось ответить: {escape(e.__class__.__name__)}"

    def voice_text(self, voice_obj: dict) -> str:
        if not self.settings.stt_model:
            return "Голосовые вопросы выключены (STT_MODEL пуст). Можно спросить текстом: /ask вопрос"
        if (voice_obj.get("duration") or 0) > 60:
            return "Голосовое длиннее минуты — спросите короче."
        from . import voice

        try:
            f = self.api.call("getFile", file_id=voice_obj["file_id"])
            audio = self.api.download(f["file_path"])
            question = voice.transcribe(audio, self.settings.stt_model,
                                        proxy=self.settings.llm_proxy or self.settings.tg_proxy)
        except Exception as e:  # noqa: BLE001
            log.exception("распознавание голосового")
            return f"Не удалось распознать голосовое: {escape(e.__class__.__name__)}. Можно спросить текстом: /ask вопрос"
        if not question:
            return "В голосовом не удалось разобрать слов."
        return self.ask_text(question)

    def queue_text(self) -> str:
        items = queries.post_queue(self.conn)
        # 'sending' в post_queue не входит — добираем отдельно, это самое важное для человека
        stuck = [dict(r) for r in self.conn.execute(
            "SELECT id, kind, status, source, created_at, attempts, last_error FROM posts "
            "WHERE status='sending' ORDER BY id")]
        known = {i["id"] for i in items}
        items = sorted(items + [s for s in stuck if s["id"] not in known], key=lambda i: i["id"])
        if not items:
            return "Очередь пуста: нет постов на модерации или в ожидании отправки."
        tz = self.settings.timezone
        lines = ["<b>Очередь постов</b>"]
        for i in items:
            line = (f"\n#{i['id']} · {KIND_RU.get(i.get('kind'), escape(str(i.get('kind'))))} · "
                    f"{POST_STATUS_RU.get(i.get('status'), escape(str(i.get('status'))))}\n"
                    f"создан {fmt_local(i.get('created_at'), tz)}")
            if i.get("source") == "mcp":
                line += " · предложено ИИ-агентом"
            if i.get("attempts"):
                line += f" · попыток {i['attempts']}"
            if i.get("last_error"):
                line += "\nошибка: " + escape(trim_lines(str(i["last_error"]), 200))
            if i.get("status") == "sending":
                line += "\n⚠️ отправка прервалась — проверьте канал вручную, бот сам не повторит"
            lines.append(line)
        return trim_lines("\n".join(lines), TEXT_LIMIT)

    # ---------- доставка ----------

    def _parts(self, payload: dict) -> list[tuple[str, dict]]:
        photos = [p for p in (payload.get("photos") or []) if p][:ALBUM_LIMIT]
        text = payload.get("text") or ""
        caption = payload.get("caption") or text
        parts: list[tuple[str, dict]] = []
        if len(photos) > 1:
            media = [{"type": "photo", "media": url} for url in photos]
            media[0].update(caption=trim_lines(caption, CAPTION_LIMIT), parse_mode="HTML")
            parts.append(("sendMediaGroup", {"media": media}))
        elif photos:
            parts.append(("sendPhoto", {"photo": photos[0], "caption": trim_lines(caption, CAPTION_LIMIT),
                                        "parse_mode": "HTML"}))
        if photos:
            if text and text.strip() != caption.strip():
                for chunk in split_text(text):
                    parts.append(("sendMessage", {"text": chunk, "parse_mode": "HTML",
                                                  "link_preview_options": {"is_disabled": True}}))
        else:
            for chunk in split_text(text or caption):
                parts.append(("sendMessage", {"text": chunk, "parse_mode": "HTML"}))
        return parts

    def deliver(self) -> int:
        channel = self.settings.publish_channel_id
        if not channel:
            log.warning("канал для публикации не задан — доставка пропущена")
            return 0
        now = self._now()
        rows = self.conn.execute(
            "SELECT id FROM posts WHERE status='approved' AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
            "ORDER BY id", (kdb.iso(now),)).fetchall()
        sent = 0
        for r in rows:
            claimed = self.conn.execute("UPDATE posts SET status='sending' WHERE id=? AND status='approved'",
                                        (r["id"],))
            self.conn.commit()
            if claimed.rowcount == 0:
                continue
            row = self._post(r["id"])
            try:
                self._send_post(row, channel)
            except TelegramRetryAfter as e:
                self._fail(row, str(e), timedelta(seconds=max(1, e.retry_after)), final_ok=False)
                break  # лимит общий на бота — остальным тоже ждать
            except Exception as e:  # noqa: BLE001 — сеть, 5xx, 4xx: всё в повтор с паузой
                delay = min(BACKOFF_BASE_S * 2 ** (row["attempts"] or 0), BACKOFF_MAX_S)
                self._fail(row, str(e), timedelta(seconds=delay), final_ok=True)
                continue
            self.conn.execute("UPDATE posts SET status='sent', sent_at=?, target_chat=?, last_error=NULL "
                              "WHERE id=?", (kdb.iso(self._now()), channel, row["id"]))
            self.conn.execute("DELETE FROM kv WHERE key=?", (f"tg_post_progress:{row['id']}",))
            self.conn.commit()
            sent += 1
        return sent

    def _send_post(self, row: sqlite3.Row, channel: str) -> None:
        parts = self._parts(json.loads(row["payload"]))
        key = f"tg_post_progress:{row['id']}"
        done = int(kdb.kv_get(self.conn, key, "0") or 0)
        # уже отправленные части (альбом до упавшего текста) при повторе не шлём заново
        for idx, (method, params) in enumerate(parts):
            if idx < done:
                continue
            params, files = self._with_uploads(method, params)
            self.api.call(method, files=files, chat_id=channel, **params)
            kdb.kv_set(self.conn, key, str(idx + 1))
            self.conn.commit()

    def _with_uploads(self, method: str, params: dict) -> tuple[dict, dict | None]:
        """Заменяет ссылки на фото загруженными файлами (если задан photo_fetch)."""
        if not self.photo_fetch or method not in ("sendPhoto", "sendMediaGroup"):
            return params, None
        params = json.loads(json.dumps(params))
        files: dict[str, tuple[str, bytes]] = {}

        def upload(url: str) -> str:
            name = f"p{len(files)}"
            # исходник бывает тяжёлым, Telegram всё равно пережмёт — берём 1200x900
            src = url[:-len("/orig")] + "/1200x900" if url.endswith("/orig") else url
            data = self.photo_fetch(src)
            if not data.startswith(b"\xff\xd8"):  # CDN иногда отдаёт webp — Telegram надёжнее принимает JPEG
                import io

                from PIL import Image
                buf = io.BytesIO()
                Image.open(io.BytesIO(data)).convert("RGB").save(buf, "JPEG", quality=90)
                data = buf.getvalue()
            files[name] = (f"{name}.jpg", data)
            return f"attach://{name}"

        if method == "sendPhoto":
            params["photo"] = upload(params["photo"])
        else:
            for item in params["media"]:
                item["media"] = upload(item["media"])
        return params, files

    def _fail(self, row: sqlite3.Row, error: str, delay: timedelta, final_ok: bool) -> None:
        attempts = (row["attempts"] or 0) + 1
        status = "failed" if final_ok and attempts >= MAX_ATTEMPTS else "approved"
        self.conn.execute("UPDATE posts SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                          (status, attempts, kdb.iso(self._now() + delay), error[:500], row["id"]))
        self.conn.commit()
        log.warning("пост #%s: попытка %s не удалась (%s), %s", row["id"], attempts, error,
                    "больше не пробуем" if status == "failed" else f"повтор через {int(delay.total_seconds())} с")

    # ---------- уведомления и цикл ----------

    def send_admin(self, text: str) -> bool:
        if not self.admin_chat:
            log.warning("TG_ADMIN_CHAT_ID не задан — уведомление не отправлено")
            return False
        try:
            for chunk in split_text(text):
                self.api.call("sendMessage", chat_id=self.admin_chat, text=chunk)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("уведомление в админ-чат не ушло: %s", e)
            return False

    def poll_once(self, timeout: int = 25) -> int:
        offset = int(kdb.kv_get(self.conn, OFFSET_KEY, "0") or 0)
        updates = self.api.call("getUpdates", offset=offset or None, timeout=timeout,
                                allowed_updates=["message", "callback_query"]) or []
        handled = 0
        for upd in updates:
            kinds = [k for k in upd if k != "update_id"]
            obj = upd.get(kinds[0]) if kinds else {}
            obj = obj if isinstance(obj, dict) else {}
            chat = (obj.get("chat") or (obj.get("message") or {}).get("chat") or {})
            text = obj.get("text") or obj.get("data") or ""
            # без содержимого: только тип, чат и признак команды — чтобы разбирать «бот молчит»
            log.info("обновление %s: %s, чат %s (%s), команда=%s", upd.get("update_id"), ",".join(kinds),
                     chat.get("id"), chat.get("type"), str(text).startswith("/"))
            try:
                self.handle_update(upd)
            except Exception:  # noqa: BLE001 — одно кривое обновление не должно стопорить очередь
                log.exception("ошибка обработки обновления %s", upd.get("update_id"))
            kdb.kv_set(self.conn, OFFSET_KEY, str(int(upd["update_id"]) + 1))
            self.conn.commit()
            handled += 1
        return handled

    def tick(self) -> None:
        self.push_to_moderation()
        self.deliver()

    def run_forever(self, stop_event: threading.Event | None = None, poll_timeout: int = 25,
                    error_sleep: float = 5.0) -> None:
        stop_event = stop_event or threading.Event()
        while not stop_event.is_set():
            try:
                self.poll_once(timeout=poll_timeout)
                self.tick()
            except Exception as e:  # noqa: BLE001
                log.error("цикл бота: %s", e)
                stop_event.wait(error_sleep)
