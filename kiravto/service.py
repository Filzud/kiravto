"""Что происходит вокруг прогона: посты, факты LLM, отчёт, оповещения, расписание."""
from __future__ import annotations

import logging
import random
import threading
from datetime import datetime

from . import posts as P
from .collector import Collector
from .config import Settings
from .db import connect, kv_get, kv_set, utcnow
from .source.parse import SourceFormatError

log = logging.getLogger("kiravto.service")


def make_bot(conn, settings: Settings):
    """Бот для оповещений и модерации. None — Telegram не настроен."""
    if not (settings.tg_token and settings.tg_admin_chat_id):
        return None
    from .telegram_bot import BotAPI, ModerationBot

    return ModerationBot(conn, settings, BotAPI(settings.tg_api_base, settings.tg_token, settings.tg_proxy))


def make_facts(conn, settings: Settings):
    from .llm import FactExtractor, LLMClient

    return FactExtractor(conn, settings, LLMClient(settings) if settings.llm_enabled else None)


def check_incomplete_alert(conn, now: datetime | None = None) -> str | None:
    """Два неполных/упавших прогона подряд → текст оповещения (один раз на серию)."""
    runs = conn.execute("SELECT id, status, note FROM runs ORDER BY id DESC LIMIT 2").fetchall()
    if len(runs) < 2 or any(r["status"] == "complete" for r in runs):
        return None
    if kv_get(conn, "alerted_incomplete_run") == str(runs[1]["id"]):
        return None  # про эту серию уже сообщили
    kv_set(conn, "alerted_incomplete_run", str(runs[1]["id"]))
    conn.commit()
    notes = "; ".join(f"#{r['id']}: {r['note'] or r['status']}" for r in runs)
    return f"⚠️ Два неполных прогона подряд. Снятия не считаются, пока не будет полного прогона.\n{notes}"


def after_run(conn, settings: Settings, summary: dict, now: datetime | None = None) -> dict:
    now = now or utcnow()
    out: dict = {}
    try:
        out["llm"] = make_facts(conn, settings).process_pending(limit=40)
    except Exception as e:  # noqa: BLE001 — LLM необязательна
        log.warning("LLM: %s", e)
    if summary.get("status") == "complete":
        out["drop_posts"] = len(P.create_drop_posts(conn, settings.drop_min_pct, now=now, tz=settings.timezone))
        if P.due_daily(now, settings.daily_post_time, settings.timezone):
            out["daily_post"] = P.create_daily_post(conn, now=now, tz=settings.timezone)
    try:
        from .report import render_report

        out["report"] = str(render_report(conn, settings.report_path, now=now, settings=settings))
    except ImportError:
        pass
    alert = check_incomplete_alert(conn, now)
    if alert:
        bot = make_bot(conn, settings)
        out["alert_sent"] = bool(bot and bot.send_admin(alert))
        log.warning(alert)
    return out


def run_once(settings: Settings, source=None) -> dict:
    from .photohash import make_fetcher
    from .source.autoru import AutoruBrowserSource

    conn = connect(settings.db_path)
    src = source or AutoruBrowserSource(settings.dealer_code, settings.dealer_id, headless=settings.headless,
                                        pause=settings.request_pause_s)
    try:
        try:
            summary = Collector(conn, src, settings, photo_fetch=make_fetcher()).run()
        except SourceFormatError as e:
            summary = dict(conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
            summary["error"] = str(e)
        summary["after"] = after_run(conn, settings, summary)
        return summary
    finally:
        src.close()
        conn.close()


def collector_loop(settings: Settings, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            s = run_once(settings)
            log.info("прогон #%s: %s, собрано %s/%s, запросов %s", s.get("id"), s.get("status"), s.get("collected"),
                     s.get("source_total"), s.get("requests"))
        except Exception:  # noqa: BLE001 — цикл не должен умирать
            log.exception("прогон упал")
        # 30–60 минут: базовый интервал ± джиттер, чтобы не ходить к источнику по часам
        wait = settings.run_interval_min * 60 * random.uniform(0.85, 1.15)
        wait = min(max(wait, 30 * 60), 60 * 60) if settings.run_interval_min >= 30 else wait
        stop.wait(wait)


def bot_loop(settings: Settings, stop: threading.Event | None = None) -> None:
    conn = connect(settings.db_path)
    bot = make_bot(conn, settings)
    if bot is None:
        # не выходим: в docker compose с restart=unless-stopped выход превратился бы в бесконечный перезапуск
        log.error("Telegram не настроен: нужны TG_BOT_TOKEN и TG_ADMIN_CHAT_ID в .env. Бот ждёт, посты копятся в очереди.")
        (stop or threading.Event()).wait()
        return
    bot.run_forever(stop)

