"""HTML-отчёт по складу: один самодостаточный файл (стили внутри, без внешних ресурсов).

Пересобирается после каждого прогона. Числа, которые нельзя посчитать честно, не выдумываем —
пишем, почему их нет. Пустые значения выводим как «—».
"""
from __future__ import annotations

import html
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import queries
from .config import Settings
from .db import iso, parse_iso, utcnow

DASH = "—"
WEEK = timedelta(days=7)

ANOMALY_LABELS = {
    "price_too_low": "цена ≤ 1000 ₽",
    "zero_mileage_used": "нет пробега у машины с пробегом",
    "year_in_future": "год выпуска в будущем",
    "year_too_old": "год выпуска раньше 1950",
    "price_10x_below_median": "цена в 10+ раз ниже медианы модели",
    "mileage_too_high": "пробег больше 1,5 млн км",
}
RUN_STATUS = {"complete": "полный", "incomplete": "неполный", "failed": "упал", "running": "идёт"}
SECTION = {"new": "новые", "used": "с пробегом"}


# ------------------------------------------------------------------ формат
def _tz(settings: Settings | None):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(settings.timezone if settings else "Europe/Moscow")
    except Exception:  # на Windows без пакета tzdata зон нет; Москва круглый год UTC+3
        return timezone(timedelta(hours=3), "МСК")


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def esc(v) -> str:
    return DASH if _empty(v) or v == "" else html.escape(str(v))


def num(v, digits: int = 0) -> str:
    if _empty(v):
        return DASH
    s = f"{v:,.{digits}f}".replace(",", " ")
    return s.replace(".", ",")


def rub(v) -> str:
    return DASH if _empty(v) else num(v) + " ₽"


def pct(v, digits: int = 1) -> str:
    return DASH if _empty(v) else num(v, digits) + " %"


def share(part: int, total: int) -> str:
    return pct(part / total * 100) if total else DASH


class _Fmt:
    def __init__(self, tz):
        self.tz = tz

    def dt(self, s: str | None) -> str:
        if not s:
            return DASH
        try:
            return parse_iso(s).astimezone(self.tz).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return esc(s)


# ------------------------------------------------------------------ расчёты
def stock_now_and_week_ago(conn: sqlite3.Connection, now: datetime) -> dict:
    active = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]
    cutoff = now - WEEK
    first = conn.execute("SELECT MIN(started_at) FROM runs").fetchone()[0]
    out = {"active": active, "cutoff": iso(cutoff), "db_since": first, "week_ago": None, "young": True}
    if first and parse_iso(first) <= cutoff:
        out["young"] = False
        out["week_ago"] = conn.execute(
            "SELECT COUNT(*) FROM offers WHERE first_seen<=? AND (removed_at IS NULL OR removed_at>?)",
            (iso(cutoff), iso(cutoff))).fetchone()[0]
    return out


def exposure(conn: sqlite3.Connection) -> dict:
    """Срок от появления на сайте (или первого нашего взгляда) до снятия, по снятым и проданным."""
    days = []
    from_source = 0
    for r in conn.execute("SELECT removed_at, created_at_source, first_seen FROM offers "
                          "WHERE status IN ('removed','sold') AND removed_at IS NOT NULL"):
        start = r["created_at_source"] or r["first_seen"]
        from_source += bool(r["created_at_source"])
        d = (parse_iso(r["removed_at"]) - parse_iso(start)).total_seconds() / 86400
        days.append(max(d, 0.0))
    return {"n": len(days), "from_source": from_source,
            "mean": statistics.fmean(days) if days else None,
            "median": statistics.median(days) if days else None}


def model_prices(conn: sqlite3.Connection, top: int = 25) -> list[dict]:
    groups: dict[tuple, list[int]] = {}
    for r in conn.execute("SELECT mark, model, price FROM offers WHERE status='active' AND price IS NOT NULL"):
        groups.setdefault((r["mark"], r["model"]), []).append(r["price"])
    rows = [{"mark": k[0], "model": k[1], "count": len(v), "median": statistics.median(v),
             "min": min(v), "max": max(v)} for k, v in groups.items()]
    rows.sort(key=lambda d: (-d["count"], str(d["mark"]), str(d["model"])))
    return rows[:top]


def anomalies(conn: sqlite3.Connection) -> dict:
    total = with_any = 0
    by_code: dict[str, int] = {}
    for r in conn.execute("SELECT anomalies FROM offers WHERE status='active'"):
        total += 1
        codes = json.loads(r["anomalies"]) if r["anomalies"] else []
        if codes:
            with_any += 1
        for c in codes:
            by_code[c] = by_code.get(c, 0) + 1
    return {"total": total, "with_any": with_any,
            "by_code": sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0]))}


def runs_week(conn: sqlite3.Connection, now: datetime) -> dict:
    rows = conn.execute("SELECT status, COUNT(*) n FROM runs WHERE started_at>=? AND status!='running' "
                        "GROUP BY status", (iso(now - WEEK),)).fetchall()
    by = {r["status"]: r["n"] for r in rows}
    total = sum(by.values())
    return {"total": total, "complete": by.get("complete", 0), "incomplete": by.get("incomplete", 0),
            "failed": by.get("failed", 0)}


# ------------------------------------------------------------------ вёрстка
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#667085;--line:#e4e7ec;--accent:#0b63ce;--bad:#b42318;--good:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--muted:#98a2b3;--line:#2c3340;--accent:#5aa2ff;--bad:#f97066;--good:#47cd89}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px}
.muted{color:var(--muted)}.note{color:var(--muted);font-size:13px;margin:6px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:13px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-weight:600;color:var(--muted);font-size:13px}td.r,th.r{text-align:right}tr:last-child td{border-bottom:0}
a{color:var(--accent)}.bad{color:var(--bad)}.good{color:var(--good)}
"""


def _table(head: list[tuple[str, bool]], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{html.escape(empty)}</p>'
    th = "".join(f'<th class="r">{h}</th>' if right else f"<th>{h}</th>" for h, right in head)
    body = "".join("<tr>" + "".join(f'<td class="r">{c}</td>' if head[i][1] else f"<td>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


OFFER_STATUS = {"active": "в продаже", "removed": "снято", "sold": "вероятно продано"}


def _cars(n: int | None) -> str:
    n = n or 0
    if n % 10 == 1 and n % 100 != 11:
        return "машина"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "машины"
    return "машин"


def build_html(conn: sqlite3.Connection, now: datetime | None = None, settings: Settings | None = None) -> str:
    now = now or utcnow()
    f = _Fmt(_tz(settings))
    stock = stock_now_and_week_ago(conn, now)
    exp = exposure(conn)
    models = model_prices(conn)
    drops = queries.price_drops(conn, days=7, min_pct=0, now=now)[:15]
    an = anomalies(conn)
    rw = runs_week(conn, now)
    last = queries.last_runs(conn, 10)
    success = queries.last_success(conn)

    parts = [f"<h1>Склад РОЛЬФ Ясенево</h1><p class=\"muted\">Сформировано {f.dt(iso(now))}. "
             f"Последний полный прогон: {f.dt(success['finished_at']) if success else 'ещё не было'}.</p>"]

    # --- склад сейчас и неделю назад
    if stock["young"]:
        week_tile = (f"<b>{DASH}</b><span>неделю назад: база моложе 7 дней "
                     f"(первый прогон {f.dt(stock['db_since'])}), честно посчитать нельзя</span>")
        week_note = ""
    else:
        diff = stock["active"] - stock["week_ago"]
        week_tile = f"<b>{num(stock['week_ago'])}</b><span>было неделю назад ({f.dt(stock['cutoff'])})</span>"
        week_note = (f'<p class="note">За неделю: {"+" if diff > 0 else ""}{num(diff)}. «Неделю назад» восстановлено '
                     "по датам первого появления и снятия; машины, снятые и вернувшиеся с тем же ID, "
                     "считаются бывшими в продаже.</p>")
    exp_tile = (f"<b>{num(exp['mean'], 1)} дн.</b><span>средний срок экспозиции до снятия "
                f"(медиана {num(exp['median'], 1)} дн.), по {num(exp['n'])} машинам</span>") if exp["n"] else \
        f"<b>{DASH}</b><span>срок экспозиции: снятых машин пока нет</span>"
    parts.append('<section><h2>Склад</h2><div class="tiles">'
                 f'<div class="tile"><b>{num(stock["active"])}</b><span>{_cars(stock["active"])} в продаже сейчас</span></div>'
                 f'<div class="tile">{week_tile}</div><div class="tile">{exp_tile}</div>'
                 f'<div class="tile"><b>{share(an["with_any"], an["total"])}</b>'
                 f'<span>доля объявлений с аномалиями ({num(an["with_any"])} из {num(an["total"])})</span></div>'
                 f'<div class="tile"><b>{share(rw["incomplete"] + rw["failed"], rw["total"])}</b>'
                 f'<span>неполных прогонов за 7 дней ({num(rw["incomplete"] + rw["failed"])} из {num(rw["total"])})'
                 '</span></div></div>' + week_note)
    if exp["n"]:
        parts.append(f'<p class="note">Срок экспозиции = дата снятия − дата размещения на Авто.ру '
                     f'(есть у {num(exp["from_source"])} из {num(exp["n"])}); если её нет — от первого появления '
                     "в нашей базе, тогда срок занижен.</p>")
    parts.append("</section>")

    # --- медианы по моделям
    parts.append("<section><h2>Медианная цена по моделям</h2>" + _table(
        [("Марка", False), ("Модель", False), ("Машин", True), ("Медиана", True), ("Мин.", True), ("Макс.", True)],
        [[esc(m["mark"]), esc(m["model"]), num(m["count"]), rub(m["median"]), rub(m["min"]), rub(m["max"])]
         for m in models], "Машин в продаже нет.")
        + '<p class="note">Топ-25 моделей по числу машин в продаже.</p></section>')

    # --- снижения цены
    parts.append("<section><h2>Самые сильные снижения цены за неделю</h2>" + _table(
        [("Машина", False), ("Год", True), ("Было", True), ("Стало", True), ("Снижение", True), ("Статус", False)],
        [[f'<a href="{html.escape(d["url"] or "", quote=True)}">{esc(d["mark"])} {esc(d["model"])}</a>',
          esc(d["year"]), rub(d["old_price"]), rub(d["new_price"]),
          f'<span class="good">−{pct(d["drop_pct"])}</span> ({rub(d["drop_rub"])})',
          esc(OFFER_STATUS.get(d["status"], d["status"]))]
         for d in drops], "За последние 7 дней снижений цены не было.")
        + '<p class="note">Первая цена в окне 7 дней против последней.</p></section>')

    # --- аномалии
    parts.append("<section><h2>Аномалии</h2>" + _table(
        [("Аномалия", False), ("Машин", True), ("Доля склада", True)],
        [[esc(ANOMALY_LABELS.get(code, code)), num(n), share(n, an["total"])] for code, n in an["by_code"]],
        "Аномалий в объявлениях в продаже нет.")
        + '<p class="note">Аномалии только помечаются, из данных ничего не выбрасывается.</p></section>')

    # --- LLM-функция
    llm = queries.llm_stats(conn)
    enabled = bool(settings and settings.llm_enabled)
    if llm["calls"]:
        parts.append("<section><h2>Факты из описаний (LLM)</h2><div class=\"tiles\">"
                     f'<div class="tile"><b>{num(llm["facts_kept"])}</b><span>фактов оставлено (цитата найдена)</span></div>'
                     f'<div class="tile"><b>{num(llm["facts_dropped"])}</b><span>фактов выброшено (цитаты нет в тексте)</span></div>'
                     f'<div class="tile"><b>{num(llm["calls"])}</b><span>описаний отправлено в модель, '
                     f'${llm["cost_usd"]:.4f}</span></div></div></section>')
    else:
        parts.append("<section><h2>Факты из описаний (LLM)</h2><p class=\"note\">"
                     + ("Функция включена, но описаний в модель ещё не отправлялось." if enabled
                        else "Функция выключена: не задан ключ LLM. Остальной сервис работает без неё.")
                     + "</p></section>")

    # --- прогоны
    rows = []
    for r in last:
        st = RUN_STATUS.get(r["status"], r["status"])
        cls = "good" if r["status"] == "complete" else "bad"
        coll = f'{num(r["collected"])} / {num(r["source_total"])}'
        rows.append([f.dt(r["started_at"]), (num(r["duration_s"], 1) + " с") if not _empty(r["duration_s"])
                     else DASH, num(r["requests"]), num(r["errors"]), coll,
                     # полнота — собрано от заявленного самим источником (а не от прошлого склада: там бывает >100%)
                     pct(r["collected"] / r["source_total"] * 100 if r["source_total"] else None, 0),
                     num(r["new_count"]), num(r["changed_count"]), num(r["removed_count"]),
                     f'<span class="{cls}">{esc(st)}</span>', esc(r["note"])])
    parts.append("<section><h2>Последние прогоны</h2>" + _table(
        [("Начало", False), ("Длительность", True), ("Запросов", True), ("Ошибок", True),
         ("Собрано / заявлено", True), ("Полнота", True), ("Новых", True), ("Изменений", True), ("Снято", True),
         ("Статус", False), ("Заметка", False)], rows, "Прогонов ещё не было.")
        + f'<p class="note">За 7 дней прогонов: {num(rw["total"])}, полных {num(rw["complete"])}, '
          f'неполных {num(rw["incomplete"])}, упавших {num(rw["failed"])}. Полнота — собрано относительно '
          "того, сколько заявил сам источник.</p></section>")

    body = "\n".join(parts)
    return ("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Склад РОЛЬФ Ясенево</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def render_report(conn: sqlite3.Connection, path: Path | str, now: datetime | None = None,
                  settings: Settings | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(build_html(conn, now=now, settings=settings), encoding="utf-8")
    tmp.replace(path)  # атомарно: API не отдаст наполовину записанный файл
    return path
