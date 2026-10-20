"""Командная строка: python -m kiravto <команда>."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
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
            out.append(f"{k}={current.get(k, '')}")
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
    uvicorn.run(create_app(s), host=args.host, port=args.port or s.api_port)


def cmd_serve(args) -> None:
    """Всё в одном процессе: сборщик по расписанию + бот + API (для локального запуска без docker)."""
    from .service import bot_loop, collector_loop

    s = load_settings()
    threading.Thread(target=collector_loop, args=(s,), daemon=True, name="collector").start()
    if s.tg_token:
        threading.Thread(target=bot_loop, args=(s,), daemon=True, name="bot").start()
    cmd_api(args)


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
    rows = conn.execute("SELECT * FROM offers WHERE status='active'").fetchall()
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    sp = sub.add_parser("status", help="последние прогоны и очередь постов")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(f=cmd_status)
    sp = sub.add_parser("sample", help="N случайных машин для сверки")
    sp.add_argument("n", type=int, nargs="?", default=20)
    sp.add_argument("--seed", type=int, default=None)
    sp.set_defaults(f=cmd_sample)
    sub.add_parser("report", help="пересобрать HTML-отчёт").set_defaults(f=cmd_report)
    sp = sub.add_parser("dump-fixtures", help="сохранить свежие ответы источника")
    sp.add_argument("--pages", type=int, default=1)
    sp.add_argument("--out", default="recon/fresh")
    sp.set_defaults(f=cmd_dump_fixtures)
    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
