"""Командная строка: python -m kiravto <команда>."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import random
import sys
import threading
from pathlib import Path

from .config import ROOT, load_env_file, load_settings
from .db import connect

SECRET_KEYS = [
    ("TG_BOT_TOKEN", "Токен Telegram-бота (от @BotFather)"),
    ("API_TOKEN", "Токен для HTTP API (Enter — сгенерировать)"),
    ("LLM_API_KEY", "Ключ LLM (Enter — пропустить, функция фактов будет выключена)"),
]
PLAIN_KEYS = [
    ("TG_ADMIN_CHAT_ID", "ID админ-чата (группы), например -1001234567890"),
    ("TG_TEST_CHANNEL_ID", "ID тестового канала"),
    ("TG_PROD_CHANNEL_ID", "ID боевого канала (включается отдельно, руками в .env)"),
    ("TG_PROXY", "Прокси для Telegram, если нужен (например http://127.0.0.1:10809)"),
    ("LLM_PROVIDER", "Провайдер LLM: openrouter / openai / anthropic (Enter — пропустить)"),
    ("LLM_MODEL", "Модель LLM, например openai/gpt-4o-mini"),
    ("LLM_PROXY", "Прокси для LLM, если нужен (например http://127.0.0.1:10809)"),
]


def cmd_secrets(_args) -> None:
    """Скрытый ввод секретов в .env. Значения не печатаются ни здесь, ни в логах."""
    env_path = ROOT / ".env"
    current = load_env_file(env_path) or load_env_file(ROOT / ".env.example")
    print(f"Запись в {env_path}. Enter — оставить как есть. Секреты вводятся скрыто.")
    for key, prompt in SECRET_KEYS:
        mark = "задан" if current.get(key) else "пусто"
        val = getpass.getpass(f"{prompt} [{mark}]: ").strip()
        if not val and key == "API_TOKEN" and not current.get(key):
            import secrets
            val = secrets.token_urlsafe(32)
            print("  API_TOKEN сгенерирован (посмотреть можно только в самом .env)")
        if val and key == "TG_BOT_TOKEN":
            shape = token_shape(val)
            if "формат верный" not in " ".join(shape):
                print("  ⚠ токен выглядит неправильно: " + "; ".join(shape[1:-1] or shape))
                if input("  Сохранить всё равно? (y/N): ").strip().lower() != "y":
                    print("  Не сохранён — запустите команду ещё раз.")
                    continue
        if val:
            current[key] = val
    for key, prompt in PLAIN_KEYS:
        val = input(f"{prompt} [{current.get(key, '')}]: ").strip()
        if val:
            current[key] = val
    current.setdefault("TG_PUBLISH_TARGET", "test")
    example = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    out, written = [], set()
    for line in example:
        k = line.split("=", 1)[0].strip()
        if line and not line.startswith("#") and "=" in line:
            # ключа ещё нет в .env (новая настройка) — значение по умолчанию из шаблона, а не пустое
            out.append(f"{k}={current.get(k, line.split('=', 1)[1].strip())}")
            written.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in current.items() if k not in written]
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("Готово. Боевой канал по-прежнему выключен (TG_PUBLISH_TARGET=test).")


def cmd_run_once(_args) -> None:
    from .service import run_once

    s = run_once(load_settings())
    keys = ["id", "status", "source_total", "collected", "completeness", "requests", "errors", "blocked", "new_count",
            "changed_count", "removed_count", "returned_count", "relisted_count", "sold_count", "cards_fetched",
            "details_pending", "duration_s", "note"]
    print(json.dumps({k: s.get(k) for k in keys} | {"after": s.get("after"), "error": s.get("error")},
                     ensure_ascii=False, indent=1, default=str))


def cmd_collector(_args) -> None:
    from .service import collector_loop

    collector_loop(load_settings())


def cmd_bot(_args) -> None:
    from .service import bot_loop

    bot_loop(load_settings())


def cmd_api(args) -> None:
    import uvicorn

    from .api import create_app

    s = load_settings()
    # log_config=None: иначе uvicorn перенастраивает logging и в режиме serve пропадают логи сборщика
    uvicorn.run(create_app(s), host=args.host, port=args.port or s.api_port, log_config=None)


def cmd_serve(args) -> None:
    """Всё в одном процессе: сборщик по расписанию + бот + API (для локального запуска без docker)."""
    from .service import bot_loop, collector_loop

    s = load_settings()
    threading.Thread(target=collector_loop, args=(s,), daemon=True, name="collector").start()
    if s.tg_token:
        threading.Thread(target=bot_loop, args=(s,), daemon=True, name="bot").start()
    cmd_api(args)


def token_shape(token: str) -> list[str]:
    """Описание формы токена без самого значения: длина, формат, типичные ошибки вставки."""
    import re

    t = token or ""
    lines = [f"длина {len(t)} символов (обычно 44–46)"]
    if re.fullmatch(r"\d{8,10}:[A-Za-z0-9_-]{35}", t):
        lines.append("формат верный (цифры:35 символов) — значит, токен отозван или от другого бота; "
                     "возьмите свежий в @BotFather → /mybots → API Token")
        return lines
    if any(ord(c) < 32 for c in t):
        lines.append("есть управляющие символы — при скрытом вводе нажали Ctrl+V; вставляйте ПРАВОЙ кнопкой мыши")
    if t != t.strip() or " " in t:
        lines.append("есть пробелы")
    if t.lower().startswith("bot"):
        lines.append("начинается со слова bot — его не нужно, только сам токен")
    if ":" not in t:
        lines.append("нет двоеточия — вставилось не целиком")
    elif not re.fullmatch(r"\d{8,10}", t.split(":", 1)[0].strip()):
        lines.append("до двоеточия должны быть только цифры")
    elif len(t.split(":", 1)[1]) != 35:
        lines.append(f"после двоеточия {len(t.split(':', 1)[1])} символов вместо 35 — вставилось не целиком")
    lines.append("Исправить: снова `python -m kiravto secrets` и вставить токен правой кнопкой мыши.")
    return lines


def tg_check(settings, api) -> list[str]:
    """Проверка настройки Telegram. Возвращает строки для человека; токен не печатается."""
    from .telegram_bot import TelegramError

    out: list[str] = []
    try:
        me = api.call("getMe")
    except TelegramError as e:
        out.append(f"✗ бот не отвечает: {e}.")
        if "404" in str(e) or "401" in str(e):
            out += ["Telegram не узнал токен. Форма сохранённого токена (значение не показывается):"]
            out += ["   " + line for line in token_shape(settings.tg_token)]
        else:
            out.append("Похоже на сеть: проверьте TG_PROXY (на этой машине Telegram открывается через прокси).")
        return out
    out.append(f"✓ бот @{me.get('username')} отвечает")
    try:
        updates = api.call("getUpdates", timeout=0, allowed_updates=["message", "channel_post", "my_chat_member"])
    except TelegramError as e:
        updates = []
        out.append(f"! getUpdates: {e} (если бот уже запущен в другом процессе — это нормально)")
    seen = {}
    for u in updates or []:
        for key in ("message", "channel_post", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat:
                seen[chat["id"]] = f"{chat.get('type')}: {chat.get('title') or chat.get('username') or chat.get('first_name')}"
    if seen:
        out.append("Чаты, где бот видел сообщения (ID → вписать в .env):")
        out += [f"   {cid}  {title}" for cid, title in seen.items()]
    else:
        out.append("Бот пока не видел ни одного чата: напишите что-нибудь в админ-группу и опубликуйте пост "
                   "в каждом канале, потом запустите проверку снова.")
    for name, cid in (("админ-чат", settings.tg_admin_chat_id), ("тестовый канал", settings.tg_test_channel_id),
                      ("боевой канал", settings.tg_prod_channel_id)):
        if not cid:
            out.append(f"– {name}: ID не задан")
            continue
        try:
            m = api.call("getChatMember", chat_id=cid, user_id=me["id"])
            status = m.get("status")
            ok = status in ("administrator", "creator") or (name == "админ-чат" and status == "member")
            out.append(f"{'✓' if ok else '✗'} {name} {cid}: бот — {status}"
                       + ("" if ok else " (нужны права администратора с публикацией)"))
            if name == "админ-чат" and status == "member" and not me.get("can_read_all_group_messages"):
                # режим приватности: в группе боту приходят только /команды — голосовые до него не доходят
                out.append("! в админ-чате бот не видит голосовые (режим приватности): сделайте бота администратором "
                           "группы или @BotFather → /setprivacy → Disable и добавьте бота в группу заново")
        except TelegramError as e:
            out.append(f"✗ {name} {cid}: {e}")
    prod = settings.tg_publish_target == "prod" and settings.publish_channel_id == settings.tg_prod_channel_id
    target = "БОЕВОЙ" if prod and settings.tg_prod_channel_id else "тестовый"
    out.append(f"Публикация сейчас идёт в: {target} канал")
    return out


def cmd_tg_check(_args) -> None:
    from .telegram_bot import BotAPI

    s = load_settings()
    if not s.tg_token:
        print("TG_BOT_TOKEN не задан — сначала `python -m kiravto secrets`")
        return
    print("\n".join(tg_check(s, BotAPI(s.tg_api_base, s.tg_token, s.tg_proxy))))


def cmd_mcp(_args) -> None:
    from .mcp_server import main

    main()


def cmd_status(args) -> None:
    from . import queries as Q

    conn = connect(load_settings().db_path)
    print(json.dumps({"last_success": Q.last_success(conn), "runs": Q.last_runs(conn, args.limit),
                      "queue": Q.post_queue(conn)}, ensure_ascii=False, indent=1, default=str))


def cmd_sample(args) -> None:
    """N случайных активных машин — вход для субагента-сверщика."""
    from .db import row_to_offer

    conn = connect(load_settings().db_path)
    sql = "SELECT * FROM offers WHERE status='active'" + (" AND details_at IS NOT NULL" if args.with_details else "")
    rows = conn.execute(sql).fetchall()
    pick = random.Random(args.seed).sample(rows, min(args.n, len(rows)))
    out = []
    for r in pick:
        o = row_to_offer(r)
        o["photo_count"] = len(o.pop("photos"))
        o["description"] = (o.get("description") or "")[:120]
        out.append(o)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def cmd_report(_args) -> None:
    from .report import render_report

    s = load_settings()
    print(render_report(connect(s.db_path), s.report_path, settings=s))


def cmd_market(args) -> None:
    """Поиск похожих у других продавцов сразу для N групп (штатно — понемногу в каждом прогоне)."""
    from .market import MarketScanner
    from .source.autoru import AutoruBrowserSource

    s = load_settings()
    src = AutoruBrowserSource(s.dealer_code, s.dealer_id, headless=s.headless, pause=s.request_pause_s)
    try:
        res = MarketScanner(connect(s.db_path), src, s).run(max_groups=args.groups)
    finally:
        src.close()
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_rolf_sync(_args) -> None:
    from .second_source import sync_rolf

    s = load_settings()
    print(json.dumps(sync_rolf(connect(s.db_path), s), ensure_ascii=False, indent=1))


def cmd_sheets_export(_args) -> None:
    from .gsheets import export_to_sheet

    s = load_settings()
    if not (s.gsheet_id and s.gsheet_credentials):
        print("Не настроено: нужны GSHEET_ID и GSHEET_CREDENTIALS в .env (см. README, раздел «Google Таблица»)")
        return
    print(json.dumps(export_to_sheet(connect(s.db_path), s), ensure_ascii=False, indent=1))


def cmd_ask(args) -> None:
    """Тот же ответ, что бот даёт на голосовое, — для вопроса текстом в терминале."""
    from . import voice
    from .llm import LLMClient

    s = load_settings()
    os.environ.setdefault("DB_PATH", str(s.db_path))
    print(voice.answer(" ".join(args.question), LLMClient(s) if s.llm_enabled else None))


def cmd_dump_fixtures(args) -> None:
    """Свежие ответы источника тем же путём, что ходит сервис (для скилла source-format-change)."""
    from .source.autoru import AutoruBrowserSource

    s = load_settings()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    src = AutoruBrowserSource(s.dealer_code, s.dealer_id, headless=s.headless)
    try:
        for section in ("new", "used"):
            for page in range(1, args.pages + 1):
                data = src.listing(section, page)
                (out / f"listing_{section}_p{page}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        for o in data["offers"][:2]:
            ref = f"{o['id']}-{o['hash']}"
            (out / f"card_{ref}.json").write_text(json.dumps(src.card(ref), ensure_ascii=False), "utf-8")
        print(f"сохранено в {out}, запросов {src.requests}")
    finally:
        src.close()


def main(argv: list[str] | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("KIRAVTO_LOG_FILE")
    if log_file:
        # пишем лог сами, в UTF-8: перенаправление stderr в Windows PowerShell 5.1 ломает и кодировку, и скрипт
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(log_file, encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)
    # httpx на INFO пишет каждый запрос (и полный URL, где может быть токен бота)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="kiravto")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("secrets", help="ввести токены в .env скрытым вводом").set_defaults(f=cmd_secrets)
    sub.add_parser("run-once", help="один прогон сборщика").set_defaults(f=cmd_run_once)
    sub.add_parser("collector", help="сборщик по расписанию").set_defaults(f=cmd_collector)
    sub.add_parser("bot", help="Telegram-бот модерации").set_defaults(f=cmd_bot)
    for name, f in (("api", cmd_api), ("serve", cmd_serve)):
        sp = sub.add_parser(name, help="HTTP API" if name == "api" else "сборщик + бот + API в одном процессе")
        sp.add_argument("--host", default="0.0.0.0")
        sp.add_argument("--port", type=int, default=0)
        sp.set_defaults(f=f)
    sub.add_parser("mcp", help="MCP-сервер (stdio)").set_defaults(f=cmd_mcp)
    sub.add_parser("tg-check", help="проверить бота, показать ID чатов, права в каналах").set_defaults(f=cmd_tg_check)
    sp = sub.add_parser("status", help="последние прогоны и очередь постов")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(f=cmd_status)
    sp = sub.add_parser("sample", help="N случайных машин для сверки")
    sp.add_argument("n", type=int, nargs="?", default=20)
    sp.add_argument("--seed", type=int, default=None)
    sp.add_argument("--with-details", action="store_true", help="только машины, у которых уже есть карточка")
    sp.set_defaults(f=cmd_sample)
    sub.add_parser("report", help="пересобрать HTML-отчёт").set_defaults(f=cmd_report)
    sp = sub.add_parser("market", help="«ниже рынка»: поиск похожих у других продавцов для N групп")
    sp.add_argument("--groups", type=int, default=40)
    sp.set_defaults(f=cmd_market)
    sub.add_parser("rolf-sync", help="второй источник: склад сайта rolf.ru и склейка с Авто.ру").set_defaults(
        f=cmd_rolf_sync)
    sub.add_parser("sheets-export", help="выгрузить склад и снижения в Google Таблицу").set_defaults(
        f=cmd_sheets_export)
    sp = sub.add_parser("ask", help="вопрос о складе текстом (как голосовое в админ-чате)")
    sp.add_argument("question", nargs="+")
    sp.set_defaults(f=cmd_ask)
    sp = sub.add_parser("dump-fixtures", help="сохранить свежие ответы источника")
    sp.add_argument("--pages", type=int, default=1)
    sp.add_argument("--out", default="recon/fresh")
    sp.set_defaults(f=cmd_dump_fixtures)
    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
