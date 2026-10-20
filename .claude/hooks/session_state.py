#!/usr/bin/env python3
"""SessionStart-хук: подаёт агенту состояние сервиса — последние прогоны, очередь постов, свежесть данных.

Читает только базу (не .env). Если базы нет — так и пишет.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    db = Path(os.environ.get("KIRAVTO_DB", ROOT / "data" / "kiravto.db"))
    lines = ["## Состояние сервиса Киравто (хук session_state)"]
    if not db.exists():
        lines.append(f"База {db} ещё не создана — прогонов не было. Первый прогон: `python -m kiravto run-once`.")
        print("\n".join(lines))
        return
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        runs = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 3").fetchall()
        if runs:
            lines.append("Последние прогоны:")
            for r in runs:
                lines.append(f"- #{r['id']} {r['started_at']} {r['status']}: собрано {r['collected']}"
                             f" (источник заявил {r['source_total']}), запросов {r['requests']},"
                             f" новых {r['new_count']}, изменений {r['changed_count']}, снято {r['removed_count']}"
                             + (f" — {r['note']}" if r["note"] else ""))
        ok = conn.execute("SELECT finished_at FROM runs WHERE status='complete' ORDER BY id DESC LIMIT 1").fetchone()
        if ok:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(ok[0])
            lines.append(f"Последний полный прогон: {ok[0]} ({age.total_seconds() / 3600:.1f} ч назад)")
        inc = [r["status"] for r in runs[:2]]
        if len(inc) == 2 and all(s != "complete" for s in inc):
            lines.append("ВНИМАНИЕ: два последних прогона неполные — разберись с источником, прежде чем что-то менять.")
        active = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]
        lines.append(f"Активных объявлений в базе: {active}")
        q = conn.execute("SELECT status, COUNT(*) FROM posts WHERE status IN ('new','moderation','approved','failed') "
                         "GROUP BY status").fetchall()
        lines.append("Очередь постов: " + (", ".join(f"{s}={n}" for s, n in q) if q else "пусто"))
    except sqlite3.Error as e:
        lines.append(f"Не удалось прочитать базу: {e}")
    finally:
        conn.close()
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # хук не должен ломать старт сессии
        print(f"session_state: {e}", file=sys.stderr)
