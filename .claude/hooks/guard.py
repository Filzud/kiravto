#!/usr/bin/env python3
"""PreToolUse-хук: не даёт агенту прочитать .env, записать токен в файл или включить боевой канал.

Вход — JSON от Claude Code в stdin ({"tool_name": ..., "tool_input": {...}}).
Отказ — код выхода 2 и причина в stderr (Claude Code покажет её агенту).
Хук общий: годится для любого проекта с секретами в .env.
"""
from __future__ import annotations

import json
import re
import sys

# .env, .env.local, .env.prod … но не .env.example / .env.sample / .env.template
ENV_FILE = re.compile(r"(^|[\\/\s\"'=:])\.env(?!\.(example|sample|template))(\.[\w-]+)?(?=$|[\s\"';|&)<>])")
SECRET_PATTERNS = [
    # без \b в начале: в URL токен идёт сразу после «bot» (…/bot123456:AA…/getMe)
    (re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), "токен Telegram-бота"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "ключ Anthropic"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}"), "ключ OpenRouter"),
    (re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}"), "ключ OpenAI-совместимого API"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "токен GitHub"),
]
PROD_SWITCH = re.compile(r"TG_PUBLISH_TARGET\s*[:=]\s*[\"']?prod", re.I)
SECRET_VARS = re.compile(r"\$\{?(TG_BOT_TOKEN|LLM_API_KEY|API_TOKEN)\b")
DUMP_ENV = re.compile(r"(^|[;&|]\s*)(printenv|env|set|export -p|Get-ChildItem\s+env:|gci\s+env:|dir\s+env:)\s*($|[;&|>])",
                      re.I)


def deny(reason: str) -> None:
    print(f"Заблокировано хуком guard.py: {reason}", file=sys.stderr)
    sys.exit(2)


def check(tool: str, inp: dict) -> str | None:
    """Возвращает причину отказа или None."""
    paths = [str(inp.get(k, "")) for k in ("file_path", "path", "notebook_path", "pattern", "glob") if inp.get(k)]
    text_fields = [str(inp.get(k, "")) for k in ("content", "new_string", "new_source", "command") if inp.get(k)]
    for e in inp.get("edits", []) or []:
        text_fields.append(str(e.get("new_string", "")))

    # 1. чтение/правка .env любым инструментом
    for p in paths:
        if ENV_FILE.search(" " + p.replace("\\", "/")):
            return f"доступ к файлу секретов ({p}) запрещён — секреты вводит человек командой `python -m kiravto secrets`"
    if tool in ("Bash", "PowerShell"):
        cmd = str(inp.get("command", ""))
        if ENV_FILE.search(" " + cmd.replace("\\", "/")):
            return "команда обращается к .env — секреты агенту недоступны"
        if SECRET_VARS.search(cmd):
            return "команда раскрывает переменную с секретом"
        if DUMP_ENV.search(cmd.strip()):
            return "вывод всего окружения может показать секреты"

    # 2. токены в записываемом тексте
    for t in text_fields:
        for rx, what in SECRET_PATTERNS:
            if rx.search(t):
                return f"в тексте похоже на {what} — токены в файлы и команды не пишем"

    # 3. включение боевого канала
    for t in text_fields + paths:
        if PROD_SWITCH.search(t):
            return "боевой канал включает только человек правкой .env руками"
    return None


def main() -> None:
    # Claude Code шлёт и читает UTF-8; без этого на Windows причина отказа уходит в cp1251
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        deny("хук получил не-JSON — на всякий случай отказ")
    reason = check(data.get("tool_name", ""), data.get("tool_input") or {})
    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
