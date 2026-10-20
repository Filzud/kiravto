"""Общие фикстуры: фейковый источник на сохранённых ответах Авто.ру, временная база, часы."""
from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kiravto.config import Settings
from kiravto.db import connect
from kiravto.source.parse import SourceBlocked

FIX = Path(__file__).parent / "fixtures"
AUTORU = FIX / "autoru"


def load_json(name: str) -> dict:
    return json.loads((AUTORU / name).read_text(encoding="utf-8"))


class FakeSource:
    """Отвечает как Авто.ру, но из файлов. Умеет «блокироваться» после N запросов."""

    def __init__(self, page_size: int = 5):
        self.envelopes = {s: load_json(f"listing_{s}_p1.json") for s in ("new", "used")}
        self.offers = {s: copy.deepcopy(self.envelopes[s]["offers"]) for s in ("new", "used")}
        self.cards: dict[str, dict] = {}
        self.page_size = page_size
        self.requests = 0
        self.errors = 0
        self.block_after: int | None = None
        self.card_calls: list[str] = []

    def all_offers(self) -> list[dict]:
        return self.offers["new"] + self.offers["used"]

    def find(self, oid: str) -> dict:
        return next(o for o in self.all_offers() if str(o["id"]) == oid)

    def remove(self, oid: str) -> dict:
        for s in self.offers:
            for o in self.offers[s]:
                if str(o["id"]) == oid:
                    self.offers[s].remove(o)
                    return o
        raise KeyError(oid)

    def _tick(self):
        self.requests += 1
        if self.block_after is not None and self.requests > self.block_after:
            raise SourceBlocked("тест: капча")

    def listing(self, section: str, page: int, sort: str | None = None) -> dict:
        self._tick()
        offers = self.offers[section]
        env = {k: v for k, v in self.envelopes[section].items() if k != "offers"}
        pages = max(1, math.ceil(len(offers) / self.page_size))
        env["offers"] = offers[(page - 1) * self.page_size: page * self.page_size]
        env["pagination"] = {**env["pagination"], "total_offers_count": len(offers), "total_page_count": pages,
                             "page": page, "page_size": self.page_size}
        return copy.deepcopy(env)

    def card(self, offer_ref: str) -> dict:
        self._tick()
        self.card_calls.append(offer_ref)
        if offer_ref in self.cards:
            return copy.deepcopy(self.cards[offer_ref])
        oid = offer_ref.split("-")[0]
        for o in self.all_offers():
            if str(o["id"]) == oid:
                card = copy.deepcopy(o)
                card.setdefault("description", f"Описание {oid}")
                return card
        return {"error": "OFFER_NOT_FOUND", "status": "ERROR", "detailed_error": offer_ref}

    def close(self):
        pass


class Clock:
    def __init__(self, start: datetime | None = None):
        self.now = start or datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=tmp_path / "t.db", report_path=tmp_path / "report.html", max_cards_per_run=500,
                    max_verify_per_run=50, request_pause_s=(0, 0), api_token="secret-test-token")


@pytest.fixture
def conn(settings):
    c = connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()


@pytest.fixture
def clock() -> Clock:
    return Clock()
