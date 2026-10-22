"""Выгрузка склада, снижений и прогонов в Google Таблицу через API сервисного аккаунта.

Ключ сервисного аккаунта — JSON-файл, путь в GSHEET_CREDENTIALS. Его создаёт человек
(scripts/setup_gsheets.ps1), агент файл не читает и не печатает; в git он не попадает (.gitignore: secrets/).
Таблицу создаёт человек и даёт сервисному аккаунту доступ «Редактор»: у сервисного аккаунта
нет своего места на Диске, создать файл он не может.

Без сторонних библиотек Google: JWT (RS256) подписывается `cryptography`, токен и запросы — httpx.
Каждая выгрузка перезаписывает листы целиком: в таблице всегда снимок на момент последнего прогона.
"""
from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

import httpx

from . import queries as Q
from .config import ROOT
from .db import iso, utcnow

log = logging.getLogger("kiravto.gsheets")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"

STOCK_COLUMNS = [
    ("id", "ID"), ("url", "Ссылка"), ("section", "Раздел"), ("mark", "Марка"), ("model", "Модель"),
    ("generation", "Поколение"), ("year", "Год"), ("mileage", "Пробег"), ("price", "Цена"),
    ("price_discount", "Цена со скидками"), ("availability", "Наличие"), ("color", "Цвет"),
    ("market_pct", "К рынку, %"), ("market_median", "Медиана рынка"), ("market_n", "Похожих"),
    ("rolf_vin", "VIN (сайт салона)"), ("rolf_url", "Сайт салона"), ("first_seen", "Впервые увидели"),
]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(sa: dict, now: int | None = None) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = now or int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": sa.get("private_key_id")}
    claims = {"iss": sa["client_email"], "scope": SCOPE, "aud": sa.get("token_uri") or TOKEN_URL,
              "iat": now, "exp": now + 3600}
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(sig)}"


class SheetsClient:
    def __init__(self, credentials_path: str, proxy: str = "", http: httpx.Client | None = None):
        path = Path(credentials_path)
        # относительный путь — от корня проекта: одинаково локально и в контейнере (/app/secrets, только чтение)
        self.sa = json.loads((path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8"))
        self.http = http or httpx.Client(timeout=60, proxy=proxy or None)
        self.requests = 0
        self._token: str | None = None

    def _auth(self) -> dict:
        if not self._token:
            self.requests += 1
            r = self.http.post(self.sa.get("token_uri") or TOKEN_URL,
                               data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                     "assertion": make_jwt(self.sa)})
            if r.status_code != 200:
                # тело ответа Google без секретов: error + error_description
                raise PermissionError(f"Google не выдал токен: HTTP {r.status_code} {r.text[:200]}")
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _call(self, method: str, url: str, **kw) -> dict:
        self.requests += 1
        r = self.http.request(method, url, headers=self._auth(), **kw)
        if r.status_code == 403:
            raise PermissionError(f"нет доступа к таблице: дайте {self.sa['client_email']} права «Редактор»")
        if r.status_code >= 400:
            raise RuntimeError(f"Sheets API {method} HTTP {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def ensure_sheets(self, sheet_id: str, titles: list[str]) -> None:
        meta = self._call("GET", f"{API}/{sheet_id}", params={"fields": "sheets.properties.title"})
        have = {s["properties"]["title"] for s in meta.get("sheets", [])}
        add = [{"addSheet": {"properties": {"title": t}}} for t in titles if t not in have]
        if add:
            self._call("POST", f"{API}/{sheet_id}:batchUpdate", json={"requests": add})

    def write(self, sheet_id: str, tables: dict[str, list[list]]) -> None:
        self.ensure_sheets(sheet_id, list(tables))
        self._call("POST", f"{API}/{sheet_id}/values:batchClear", json={"ranges": [f"'{t}'" for t in tables]})
        self._call("POST", f"{API}/{sheet_id}/values:batchUpdate",
                   json={"valueInputOption": "RAW",
                         "data": [{"range": f"'{t}'!A1", "values": rows} for t, rows in tables.items()]})


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def build_tables(conn: sqlite3.Connection, settings) -> dict[str, list[list]]:
    now = utcnow()
    rolf = {r["offer_id"]: dict(r) for r in conn.execute("SELECT offer_id, vin, url FROM offer_sources")}
    stock = [[h for _, h in STOCK_COLUMNS]]
    for r in conn.execute("SELECT * FROM offers WHERE status='active' ORDER BY mark, model, price"):
        o = dict(r) | {"rolf_vin": (rolf.get(r["id"]) or {}).get("vin"), "rolf_url": (rolf.get(r["id"]) or {}).get("url")}
        stock.append([_cell(o.get(k)) for k, _ in STOCK_COLUMNS])
    drops = [["ID", "Марка", "Модель", "Год", "Пробег", "Было", "Стало", "Снижение, %", "Первое снижение в окне",
              "Ссылка"]]
    for d in Q.price_drops(conn, days=7, min_pct=0.1, now=now):
        drops.append([d["id"], _cell(d["mark"]), _cell(d["model"]), _cell(d["year"]), _cell(d["mileage"]),
                      d["old_price"], d["new_price"], d["drop_pct"], d["changes"][0]["ts"], d["url"]])
    runs = [["Прогон", "Начало (UTC)", "Статус", "Собрано", "Заявлено", "Запросов", "Ошибок", "Блокировка",
             "Изменений", "Снято", "Секунд", "Примечание"]]
    for r in Q.last_runs(conn, 50):
        runs.append([r["id"], r["started_at"], r["status"], _cell(r["collected"]), _cell(r["source_total"]),
                     _cell(r["requests"]), _cell(r["errors"]), _cell(r["blocked"]), _cell(r["changed_count"]),
                     _cell(r["removed_count"]), _cell(r["duration_s"]), _cell(r["note"])])
    info = [["Выгружено (UTC)", iso(now)], ["Салон", settings.dealer_code],
            ["Снижения", "за 7 дней, первая цена в окне против текущей"],
            ["К рынку, %", "цена против медианы похожих у других продавцов Авто.ру; пусто — меньше 5 похожих"]]
    return {"Склад": stock, "Снижения за неделю": drops, "Прогоны": runs, "О выгрузке": info}


def export_to_sheet(conn: sqlite3.Connection, settings, client: SheetsClient | None = None) -> dict:
    started = utcnow()
    run_id = conn.execute("INSERT INTO side_runs(kind, started_at) VALUES('sheets', ?)", (iso(started),)).lastrowid
    conn.commit()
    client = client or SheetsClient(settings.gsheet_credentials, proxy=settings.llm_proxy)
    tables = build_tables(conn, settings)
    status, note = "complete", None
    try:
        client.write(settings.gsheet_id, tables)
        note = ", ".join(f"{t}: {len(rows) - 1}" for t, rows in tables.items())
    except (PermissionError, RuntimeError, httpx.HTTPError) as e:
        status, note = "failed", f"{e.__class__.__name__}: {e}"[:400]
        log.warning("выгрузка в таблицу: %s", note)
    conn.execute("UPDATE side_runs SET finished_at=?, status=?, requests=?, items=?, note=? WHERE id=?",
                 (iso(utcnow()), status, client.requests, len(tables["Склад"]) - 1, note, run_id))
    conn.commit()
    return {"id": run_id, "status": status, "requests": client.requests, "note": note,
            "duration_s": round((utcnow() - started) / timedelta(seconds=1), 1)}
