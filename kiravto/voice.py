"""Голосовой вопрос в админ-чате → текст → инструмент MCP-сервера → ответ.

Распознавание — локально, faster-whisper (модель STT_MODEL, по умолчанию small): без ключей и без отправки
голоса третьим лицам. Модель скачивается с Hugging Face один раз при первом голосовом.

Вопрос сводится к одному из инструментов MCP-сервера (тех же, что видит ИИ-агент): сначала правилами
(«подешевело за неделю больше чем на 5 %» → get_price_drops(days=7, min_pct=5)), а если правила не узнали
вопрос и LLM настроена — LLM выбирает инструмент и аргументы из белого списка. Ответ собирает код, а не LLM:
цифры в ответе — ровно те, что вернул инструмент.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
from html import escape

from . import mcp_server as tools
from . import normalize as nz
from .posts import fmt_km, fmt_rub

log = logging.getLogger("kiravto.voice")

TOOLS = {
    "get_price_drops": "машины, подешевевшие за days дней минимум на min_pct %",
    "get_below_market": "машины дешевле рынка (медианы похожих у других продавцов) на min_pct %",
    "get_stock_summary": "сводка склада: сколько машин, новые/с пробегом, цены",
    "get_runs_status": "свежесть данных: последние прогоны сборщика",
    "search_offers": "поиск машин: mark, model, section (new/used), price_max, year_min",
}

_model = None
_model_lock = threading.Lock()


# ---------------------------------------------------------------- распознавание
def transcribe(audio: bytes, model_name: str, proxy: str = "") -> str:
    """OGG/Opus из Telegram → текст. Декодирует PyAV внутри faster-whisper, отдельный ffmpeg не нужен."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            # прокси нужен только на первое скачивание модели; Авто.ру и Telegram его не видят
            saved = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "HTTP_PROXY")}
            if proxy:
                os.environ["HTTPS_PROXY"] = os.environ["HTTP_PROXY"] = proxy
            try:
                _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        segments, _info = _model.transcribe(io.BytesIO(audio), language="ru", beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------- вопрос → инструмент
NUM_WORDS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
             "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15, "двадцать": 20, "тридцать": 30}


def _num(s: str) -> float | None:
    s = s.strip().lower().replace(",", ".")
    if s in NUM_WORDS:
        return NUM_WORDS[s]
    try:
        return float(s)
    except ValueError:
        return None


def _period_days(q: str) -> int:
    if re.search(r"сутк|сегодня|за день", q):
        return 1
    if "месяц" in q:
        return 30
    m = re.search(r"(\d+|\w+)\s+(?:дн|дня|дней)", q)
    if m and _num(m.group(1)):
        return int(_num(m.group(1)))
    return 7  # «за неделю» и по умолчанию


def _pct(q: str, default: float) -> float:
    m = re.search(r"(\d+(?:[.,]\d+)?|\w+)\s*(?:%|процент)", q)
    if m and _num(m.group(1)) is not None:
        return _num(m.group(1))
    return default


def _mark(q: str) -> str | None:
    # «тойоты», «лексусы», «мерседесов» — пробуем слово и его основу без окончания
    for word in re.findall(r"[a-zа-я-]+", q):
        for stem in (word, word[:-1], word[:-2], word[:-1] + "а"):
            code = nz.resolve_mark(stem) if len(stem) >= 2 else None
            if code and code in nz.MARKS:
                return code
    return None


def route(question: str) -> tuple[str, dict] | None:
    """Правила: вопрос → (инструмент, аргументы). None — не узнали."""
    q = question.lower().replace("ё", "е")
    if re.search(r"ниже рынка|дешевле рынка|выгодн|дешевле.*других|ниже медиан", q):
        return "get_below_market", {"min_pct": _pct(q, 5.0)}
    if re.search(r"подешев|снизил|снижен|упал[аи]? в цене|скинул|уценил", q):
        return "get_price_drops", {"days": _period_days(q), "min_pct": _pct(q, 0.1)}
    if re.search(r"прогон|свеж|обновлял|актуальн|когда.*собира", q):
        return "get_runs_status", {"limit": 3}
    mark = _mark(q)
    if re.search(r"найди|покажи|есть ли|какие есть|сколько стоит", q) or mark:
        args: dict = {"limit": 5}
        if mark:
            args["mark"] = mark
        if re.search(r"нов(ые|ую|ая|ых)", q):
            args["section"] = "new"
        elif "пробег" in q:
            args["section"] = "used"
        m = re.search(r"до\s+(\d+(?:[.,]\d+)?)\s*(млн|миллион|тыс)", q)
        if m:
            k = 1_000_000 if m.group(2).startswith(("млн", "миллион")) else 1_000
            args["price_max"] = int(float(m.group(1).replace(",", ".")) * k)
        return "search_offers", args
    if re.search(r"сколько|склад|сводк|всего|в наличии", q):
        return "get_stock_summary", {}
    return None


def route_llm(question: str, llm) -> tuple[str, dict] | None:
    """LLM выбирает инструмент из белого списка. Ответ без JSON или с чужим инструментом — отбрасываем."""
    system = ("Ты маршрутизатор вопросов о складе автосалона. Выбери ОДИН инструмент и аргументы. "
              "Ответь только JSON вида {\"tool\": \"...\", \"args\": {...}}.\nИнструменты:\n"
              + "\n".join(f"- {k}: {v}" for k, v in TOOLS.items()))
    try:
        text, _tin, _tout = llm.chat(system, question)
        m = re.search(r"\{.*\}", text or "", re.S)
        data = json.loads(m.group(0)) if m else {}
    except Exception as e:  # noqa: BLE001 — LLM необязательна
        log.warning("маршрутизация LLM: %s", e)
        return None
    tool, args = data.get("tool"), data.get("args") or {}
    if tool not in TOOLS or not isinstance(args, dict):
        return None
    allowed = {"days", "min_pct", "limit", "mark", "model", "section", "price_max", "year_min"}
    return tool, {k: v for k, v in args.items() if k in allowed}


def call_tool(tool: str, args: dict) -> dict:
    """Вызов инструмента MCP-сервера в процессе (та же функция, что отвечает агенту по stdio)."""
    return getattr(tools, tool)(**args)


# ---------------------------------------------------------------- ответ человеку
def _car(o: dict) -> str:
    year = f", {o['year']}" if o.get("year") else ""
    return f"<b>{escape(' '.join(p for p in (o.get('mark'), o.get('model')) if p))}</b>{year}, {fmt_km(o.get('mileage'))}"


def format_answer(tool: str, args: dict, res: dict) -> str:
    if res.get("error"):
        return "Не получилось: " + escape(str(res["error"]))
    if tool == "get_price_drops":
        head = f"Подешевели за {res['days']} дн. от {res['min_pct']:g}%: {res['count']}"
        lines = [head] + [f"\n{_car(d)}\n{fmt_rub(d['old_price'])} → {fmt_rub(d['new_price'])} "
                          f"(−{d['drop_pct']:.1f}%)\n{escape(d['url'])}" for d in res["items"][:8]]
    elif tool == "get_below_market":
        cov = res["coverage"]
        lines = [f"Дешевле рынка на {res['min_pct']:g}% и больше: {len(res['items'])} "
                 f"(оценка есть у {cov['with_estimate']} из {cov['active']} машин)"]
        lines += [f"\n{_car(o)}\n{fmt_rub(o['price'])}, медиана {fmt_rub(o['market_median'])} по {o['market_n']} "
                  f"похожим ({o['market_pct']:+.1f}%)\n{escape(o['url'])}" for o in res["items"][:8]]
    elif tool == "get_stock_summary":
        sec = res.get("by_section") or {}
        lines = [f"В продаже {res['active']}: новых {sec.get('new', 0)}, с пробегом {sec.get('used', 0)}.",
                 f"Медианная цена {fmt_rub(int(res['price_median'])) if res.get('price_median') else '—'}, "
                 f"изменений цены за 7 дней {res['price_changes_7d']}, снято {res['removed']}."]
    elif tool == "get_runs_status":
        ok = res.get("last_success") or {}
        lines = [f"Последний полный прогон #{ok.get('id', '—')}, закончен {ok.get('finished_at', '—')} (UTC): "
                 f"собрано {ok.get('collected', '—')} из {ok.get('source_total', '—')}."]
    else:
        lines = [f"Нашлось {res['total']}" + (", первые:" if res["items"] else ".")]
        lines += [f"\n{_car(o)} — {fmt_rub(o['price'])}\n{escape(o['url'])}" for o in res["items"]]
    if len(lines) == 1 and tool in ("get_price_drops", "get_below_market"):
        lines.append("Таких машин нет.")
    return "\n".join(lines)


def answer(question: str, llm=None) -> str:
    routed = route(question) or (route_llm(question, llm) if llm else None)
    if routed is None:
        return (f"Вопрос: «{escape(question)}»\nНе понял, что найти. Спросите, например: «что подешевело за неделю "
                "больше чем на 5 процентов», «что ниже рынка», «сколько машин на складе», «найди новые Тойоты».")
    tool, args = routed
    res = call_tool(tool, args)
    args_txt = ", ".join(f"{k}={v}" for k, v in args.items())
    return (f"🎙 «{escape(question)}»\n<i>инструмент MCP: {tool}({escape(args_txt)})</i>\n\n"
            + format_answer(tool, args, res))
