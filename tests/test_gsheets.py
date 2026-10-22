"""Выгрузка в Google Таблицу против фейкового Google API (httpx.MockTransport), без сети.
Ключ сервисного аккаунта генерируется в тесте — настоящих ключей в репозитории нет."""
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kiravto.collector import Collector
from kiravto.gsheets import SheetsClient, build_tables, export_to_sheet, make_jwt


@pytest.fixture
def sa_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = tmp_path / "test.sa.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": "kiravto@test.iam.gserviceaccount.com",
                             "private_key_id": "k1", "private_key": pem,
                             "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
    return p, key.public_key()


class FakeGoogle:
    def __init__(self, sheets=("Лист1",), deny=False):
        self.sheets = list(sheets)
        self.deny = deny
        self.calls = []
        self.values = {}

    def __call__(self, request: httpx.Request):
        self.calls.append(request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer ya29.test"
        if self.deny:
            return httpx.Response(403, json={"error": {"code": 403, "message": "The caller does not have permission"}})
        body = json.loads(request.content or b"{}")
        if request.method == "GET":
            return httpx.Response(200, json={"sheets": [{"properties": {"title": t}} for t in self.sheets]})
        if url.endswith("values:batchUpdate"):
            for d in body["data"]:
                self.values[d["range"]] = d["values"]
        elif url.endswith(":batchUpdate"):
            self.sheets += [r["addSheet"]["properties"]["title"] for r in body["requests"]]
        return httpx.Response(200, json={})


def client(sa_path, fake):
    return SheetsClient(str(sa_path), http=httpx.Client(transport=httpx.MockTransport(fake)))


def test_jwt_is_signed_by_service_account_key(sa_file):
    path, pub = sa_file
    sa = json.loads(path.read_text(encoding="utf-8"))
    token = make_jwt(sa, now=1_000)
    head, claims, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    pub.verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    c = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert c["iss"] == sa["client_email"] and c["scope"].endswith("/spreadsheets") and c["exp"] == 4_600


def test_export_writes_all_sheets(conn, source, settings, clock, sa_file):
    Collector(conn, source, settings, clock=clock).run()
    victim = source.offers["used"][0]
    victim["price_info"]["price"] = int(victim["price_info"]["price"] * 0.9)
    clock.advance(hours=1)
    Collector(conn, source, settings, clock=clock).run()
    settings.gsheet_id = "SHEET123"
    fake = FakeGoogle()
    res = export_to_sheet(conn, settings, client(sa_file[0], fake))
    assert res["status"] == "complete"
    assert set(fake.sheets) >= {"Склад", "Снижения за неделю", "Прогоны", "О выгрузке"}   # недостающие листы созданы
    stock = fake.values["'Склад'!A1"]
    active = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]
    assert len(stock) == active + 1 and stock[0][0] == "ID"
    drops = fake.values["'Снижения за неделю'!A1"]
    assert len(drops) == 2 and drops[1][0] == str(victim["id"])
    assert all("None" not in json.dumps(row, ensure_ascii=False) for row in stock)
    assert conn.execute("SELECT kind, status, requests FROM side_runs").fetchone()[:] == ("sheets", "complete", 5)


def test_no_access_is_reported_not_raised(conn, settings, sa_file):
    settings.gsheet_id = "SHEET123"
    res = export_to_sheet(conn, settings, client(sa_file[0], FakeGoogle(deny=True)))
    assert res["status"] == "failed" and "kiravto@test.iam.gserviceaccount.com" in res["note"]


def test_tables_without_data(conn, settings):
    t = build_tables(conn, settings)
    assert len(t["Склад"]) == 1 and len(t["Снижения за неделю"]) == 1
