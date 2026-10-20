"""LLM-функция: до 3 фактов о машине из описания продавца, каждый — с дословной цитатой.

Код сам проверяет, что цитата есть в тексте; факты без подтверждения выбрасываются и считаются.
Одно описание в модель дважды не уходит (кэш по sha256 текста). Дневной бюджет в долларах.
Нет ключа или бюджет кончился — функция молча выключается, сервис работает дальше.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import Callable

import httpx

from .config import Settings
from .db import iso, utcnow
from .queries import desc_key

log = logging.getLogger("kiravto.llm")

SYSTEM = (
    "Ты помогаешь покупателю автомобиля. Из описания продавца выпиши до 3 фактов именно об ЭТОЙ машине, "
    "полезных покупателю: число владельцев, обслуживание у дилера, гарантия, история ремонтов и ДТП, "
    "комплектация и доп. оборудование, состояние. НЕ бери рекламу салона, условия кредита/трейд-ина, "
    "общие обещания («все машины проходят проверку», «более 800 автомобилей в наличии»). "
    "Для каждого факта дай ДОСЛОВНУЮ цитату из описания (подстроку, без изменений, 3–25 слов). "
    "Если полезных фактов нет — верни пустой список. Ответ — только JSON: "
    '{"facts": [{"fact": "коротко по-русски", "quote": "дословно из текста"}]}'
)
MAX_FACTS = 3
MAX_DESC_CHARS = 6000


class LLMUnavailable(Exception):
    pass


def _norm(s: str) -> str:
    s = s.replace("ё", "е").replace("Ё", "Е").replace(" ", " ")
    s = re.sub(r"[«»“”„\"]", '"', s)
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_facts(description: str, facts: list) -> tuple[list[dict], int]:
    """Оставляет факты, чья цитата реально есть в описании. Возвращает (оставленные, выброшено)."""
    norm_desc = _norm(description)
    kept, dropped = [], 0
    for f in facts if isinstance(facts, list) else []:
        if not isinstance(f, dict):
            dropped += 1
            continue
        fact, quote = str(f.get("fact") or "").strip(), str(f.get("quote") or "").strip()
        # пробелы и регистр нормализуем, слова — нет: «один владелец» ≠ «1 владелец»
        if not fact or len(_norm(quote)) < 8 or _norm(quote) not in norm_desc:
            dropped += 1
            continue
        kept.append({"fact": fact, "quote": quote})
    if len(kept) > MAX_FACTS:
        dropped += len(kept) - MAX_FACTS
        kept = kept[:MAX_FACTS]
    return kept, dropped


def parse_model_json(text: str) -> list:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("в ответе модели нет JSON")
    data = json.loads(m.group(0))
    return data.get("facts", []) if isinstance(data, dict) else []


class LLMClient:
    """Тонкий клиент: OpenAI-совместимый (OpenRouter, OpenAI) или Anthropic Messages."""

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=60, trust_env=True)

    def complete(self, description: str) -> tuple[str, int, int]:
        user = "Описание продавца:\n" + description[:MAX_DESC_CHARS]
        if self.s.llm_provider == "anthropic":
            url = (self.s.llm_base_url or "https://api.anthropic.com") + "/v1/messages"
            r = self.http.post(url, headers={"x-api-key": self.s.llm_api_key, "anthropic-version": "2023-06-01"},
                               json={"model": self.s.llm_model, "max_tokens": 600, "system": SYSTEM,
                                     "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            return text, d["usage"]["input_tokens"], d["usage"]["output_tokens"]
        base = self.s.llm_base_url or ("https://openrouter.ai/api/v1" if self.s.llm_provider == "openrouter"
                                       else "https://api.openai.com/v1")
        r = self.http.post(base.rstrip("/") + "/chat/completions",
                           headers={"Authorization": f"Bearer {self.s.llm_api_key}"},
                           json={"model": self.s.llm_model, "max_tokens": 600, "temperature": 0,
                                 "response_format": {"type": "json_object"},
                                 "messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": user}]})
        r.raise_for_status()
        d = r.json()
        return d["choices"][0]["message"]["content"], d["usage"]["prompt_tokens"], d["usage"]["completion_tokens"]


class FactExtractor:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, client: LLMClient | None = None,
                 clock: Callable[[], datetime] = utcnow):
        self.conn = conn
        self.s = settings
        self.client = client
        self.clock = clock

    @property
    def enabled(self) -> bool:
        return self.client is not None and self.s.llm_enabled

    def _day(self) -> str:
        return self.clock().date().isoformat()

    def spent_today(self) -> float:
        row = self.conn.execute("SELECT cost FROM llm_usage WHERE day=?", (self._day(),)).fetchone()
        return row["cost"] if row else 0.0

    def budget_left(self) -> float:
        return self.s.llm_daily_budget_usd - self.spent_today()

    def cached(self, description: str) -> list[dict] | None:
        row = self.conn.execute("SELECT facts FROM llm_cache WHERE desc_sha=?", (desc_key(description),)).fetchone()
        return json.loads(row["facts"]) if row else None

    def facts_for(self, description: str | None) -> list[dict] | None:
        """Факты для описания. None — функция недоступна (нет ключа/бюджета/ошибка)."""
        if not description or not description.strip():
            return []
        hit = self.cached(description)
        if hit is not None:
            return hit
        if not self.enabled:
            return None
        if self.budget_left() <= 0:
            log.info("дневной бюджет LLM исчерпан (%.4f $)", self.spent_today())
            return None
        try:
            text, tin, tout = self.client.complete(description)
            raw = parse_model_json(text)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("LLM не ответила: %s", e.__class__.__name__)
            return None
        kept, dropped = verify_facts(description, raw)
        cost = tin / 1e6 * self.s.llm_price_in_per_m + tout / 1e6 * self.s.llm_price_out_per_m
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache(desc_sha, facts, dropped, model, tokens_in, tokens_out, cost, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (desc_key(description), json.dumps(kept, ensure_ascii=False), dropped, self.s.llm_model, tin, tout, cost,
             iso(self.clock())))
        self.conn.execute(
            "INSERT INTO llm_usage(day, calls, tokens_in, tokens_out, cost, facts_kept, facts_dropped) "
            "VALUES(?,1,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET calls=calls+1, tokens_in=tokens_in+excluded.tokens_in, "
            "tokens_out=tokens_out+excluded.tokens_out, cost=cost+excluded.cost, "
            "facts_kept=facts_kept+excluded.facts_kept, facts_dropped=facts_dropped+excluded.facts_dropped",
            (self._day(), tin, tout, cost, len(kept), dropped))
        self.conn.commit()
        return kept

    def process_pending(self, limit: int = 50) -> dict:
        """Описания активных машин без фактов → в модель (пока есть бюджет)."""
        stats = {"processed": 0, "skipped_cached": 0, "disabled": not self.enabled}
        if not self.enabled:
            return stats
        rows = self.conn.execute("SELECT id, description FROM offers WHERE status='active' AND description IS NOT NULL "
                                 "ORDER BY first_seen DESC").fetchall()
        for r in rows:
            if stats["processed"] >= limit:
                break
            if self.cached(r["description"]) is not None:
                stats["skipped_cached"] += 1
                continue
            if self.facts_for(r["description"]) is None:
                break
            stats["processed"] += 1
        return stats

    def usage(self) -> dict:
        row = self.conn.execute("SELECT * FROM llm_usage WHERE day=?", (self._day(),)).fetchone()
        tot = self.conn.execute("SELECT COALESCE(SUM(calls),0), COALESCE(SUM(cost),0), COALESCE(SUM(facts_kept),0), "
                                "COALESCE(SUM(facts_dropped),0) FROM llm_usage").fetchone()
        return {"enabled": self.enabled, "today": dict(row) if row else None,
                "budget_usd": self.s.llm_daily_budget_usd, "budget_left_usd": round(self.budget_left(), 6),
                "total_calls": tot[0], "total_cost_usd": round(tot[1], 6),
                "facts_kept": tot[2], "facts_dropped": tot[3]}
