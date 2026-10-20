"""Хуки агента: подали запрещённое — получили отказ (код 2), разрешённое проходит."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / ".claude" / "hooks" / "guard.py"
STATE = ROOT / ".claude" / "hooks" / "session_state.py"

# строки собираем по частям, чтобы в репозитории не лежало ничего похожего на настоящий токен
FAKE_TG = "1234567890:" + "AAH" + "x" * 32
FAKE_ANT = "sk-" + "ant-" + "api03-" + "y" * 40


def guard(tool, **inp):
    p = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stderr


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": str(ROOT / ".env")}),
    ("Read", {"file_path": "C:\\proj\\.env"}),
    ("Read", {"file_path": "/app/.env.local"}),
    ("Grep", {"pattern": "TOKEN", "path": ".env"}),
    ("Edit", {"file_path": ".env", "old_string": "a", "new_string": "b"}),
    ("Bash", {"command": "cat .env"}),
    ("Bash", {"command": "grep TG_ .env | head"}),
    ("Bash", {"command": "source .env && echo ok"}),
    ("Bash", {"command": "echo $TG_BOT_TOKEN"}),
    ("Bash", {"command": "printenv"}),
    ("PowerShell", {"command": "Get-Content .env"}),
    ("PowerShell", {"command": "Get-ChildItem env:"}),
    ("Write", {"file_path": "notes.md", "content": f"токен {FAKE_TG}"}),
    ("Write", {"file_path": "x.py", "content": f"KEY = '{FAKE_ANT}'"}),
    ("Edit", {"file_path": "README.md", "old_string": "a", "new_string": f"bot {FAKE_TG}"}),
    ("Bash", {"command": f"curl https://api.telegram.org/bot{FAKE_TG}/getMe"}),
    ("Write", {"file_path": "docker-compose.override.yml", "content": "environment:\n  TG_PUBLISH_TARGET: prod"}),
    ("Bash", {"command": "TG_PUBLISH_TARGET=prod python -m kiravto bot"}),
    ("Edit", {"file_path": "kiravto/config.py", "old_string": "x", "new_string": "TG_PUBLISH_TARGET=prod"}),
])
def test_guard_denies(tool, inp):
    code, err = guard(tool, **inp)
    assert code == 2, (tool, inp)
    assert "Заблокировано" in err


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": str(ROOT / ".env.example")}),
    ("Read", {"file_path": str(ROOT / "kiravto" / "config.py")}),
    ("Bash", {"command": "python -m pytest -q"}),
    ("Bash", {"command": "cp .env.example /tmp/check && git status"}),
    ("Write", {"file_path": "README.md", "content": "Токен вводится командой python -m kiravto secrets"}),
    ("Grep", {"pattern": "environment", "path": "kiravto"}),
    ("Bash", {"command": "python -m kiravto run-once"}),
])
def test_guard_allows(tool, inp):
    code, err = guard(tool, **inp)
    assert code == 0, err


def test_guard_rejects_garbage_input():
    p = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True)
    assert p.returncode == 2


def test_session_state_reports_runs_and_queue(tmp_path):
    from kiravto.db import connect
    db = tmp_path / "s.db"
    c = connect(db)
    c.execute("INSERT INTO runs(started_at, finished_at, status, collected, source_total, requests) "
              "VALUES('2026-10-20T10:00:00+00:00', '2026-10-20T10:05:00+00:00', 'complete', 453, 453, 12)")
    c.execute("INSERT INTO runs(started_at, status, collected, note) VALUES('2026-10-20T11:00:00+00:00', 'incomplete', 50, 'блокировка')")
    c.execute("INSERT INTO runs(started_at, status, collected, note) VALUES('2026-10-20T12:00:00+00:00', 'incomplete', 10, 'блокировка')")
    c.execute("INSERT INTO posts(kind, dedup_key, payload, status, created_at) VALUES('price_drop','k','{}','moderation','x')")
    c.commit(); c.close()
    p = subprocess.run([sys.executable, str(STATE)], capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "KIRAVTO_DB": str(db), "PYTHONIOENCODING": "utf-8"})
    assert p.returncode == 0
    assert "собрано 453" in p.stdout
    assert "два последних прогона неполные" in p.stdout
    assert "moderation=1" in p.stdout
