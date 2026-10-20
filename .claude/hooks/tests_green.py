#!/usr/bin/env python3
"""Stop-хук: не даёт агенту закончить, пока тесты красные.

Запускает pytest в один поток. Если упало — возвращает decision=block с хвостом вывода,
и агент продолжает работу. Защита от зацикливания: при stop_hook_active повторно не блокируем.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def python_exe() -> str:
    for cand in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}
    if data.get("stop_hook_active"):
        return
    proc = subprocess.run([python_exe(), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    if proc.returncode in (0, 5):  # 5 — тестов не найдено
        return
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-25:])
    print(json.dumps({"decision": "block",
                      "reason": "Тесты красные — работа не готова. Почини и проверь снова.\n" + tail},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
