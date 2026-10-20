"""HTTP API склада: FastAPI поверх тех же запросов, что у бота и MCP (kiravto.queries).

Запуск: `uvicorn --factory "kiravto.api:app_from_env" --port 8080`.
Всё, кроме /health, — только с токеном (Authorization: Bearer … или X-API-Token).
Если API_TOKEN не задан, защищённые ручки отвечают 503: открытого доступа без токена нет.
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator

from fastapi import Depends, FastAPI, Path as PathParam, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, queries
from . import normalize as nz
from .config import Settings, load_settings

log = logging.getLogger("kiravto.api")

DESCRIPTION = """
Данные склада автосалона РОЛЬФ Ясенево, собранные с Авто.ру: текущие объявления, история цен
и статусов, снижения цен, сводка и журнал прогонов сборщика.

**Доступ.** Все ручки, кроме `/health`, требуют токен: заголовок `Authorization: Bearer <токен>`
или `X-API-Token: <токен>`. Если на сервере не задан `API_TOKEN`, защищённые ручки отвечают `503`.

**Ошибки** всегда в одном виде:
`{"error": {"code": "not_found", "message": "по-русски", "details": ...}}`.
Коды: `unauthorized` (401), `not_found` (404), `method_not_allowed` (405), `validation_error` (422),
`token_not_configured` (503), `internal_error` (500).
"""

TAGS = [
    {"name": "служебное", "description": "Состояние сервиса и прогонов сборщика."},
    {"name": "объявления", "description": "Поиск машин, карточка, история цен и событий."},
    {"name": "аналитика", "description": "Снижения цен, сводка склада, HTML-отчёт."},
]

ERROR_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
               405: "method_not_allowed", 422: "validation_error", 500: "internal_error",
               503: "token_not_configured"}
ERROR_MESSAGES = {404: "Не найдено", 405: "Метод не поддерживается", 500: "Внутренняя ошибка сервера"}

ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "object", "properties": {
        "code": {"type": "string"}, "message": {"type": "string"}, "details": {}}}},
}
AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Нет токена или токен неверный",
          "content": {"application/json": {"schema": ERROR_SCHEMA}}},
    503: {"description": "На сервере не задан API_TOKEN — доступ закрыт",
          "content": {"application/json": {"schema": ERROR_SCHEMA}}},
}
NOT_FOUND = {404: {"description": "Объявление не найдено", "content": {"application/json": {"schema": ERROR_SCHEMA}}}}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def error_response(status: int, code: str, message: str, details: Any = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": jsonable_encoder(details)}}
    return JSONResponse(body, status_code=status, headers=headers)


def health_status(conn: sqlite3.Connection, settings: Settings, now: datetime) -> dict:
    """Честное состояние: смотрим на последний ПОЛНЫЙ прогон, а не просто на последний."""
    success = queries.last_success(conn)
    finished = [dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE status!='running' ORDER BY id DESC LIMIT 2")]
    last = queries.last_runs(conn, 1)
    last_run = last[0] if last else None
    active = conn.execute("SELECT COUNT(*) FROM offers WHERE status='active'").fetchone()[0]

    problems: list[str] = []
    if not success:
        status = "no_data"
        problems.append("ещё не было ни одного полного прогона")
    else:
        age = now - db.parse_iso(success["finished_at"] or success["started_at"])
        limit = timedelta(minutes=3 * settings.run_interval_min)
        if age > limit:
            problems.append(f"последний полный прогон {int(age.total_seconds() // 60)} мин назад "
                            f"(норма — не старше {int(limit.total_seconds() // 60)} мин)")
        if len(finished) == 2 and all(r["status"] != "complete" for r in finished):
            problems.append("два последних прогона неполные или упали")
        if age > limit:
            status = "stale"
        elif problems:
            status = "degraded"
        else:
            status = "ok"
    return {
        "status": status,
        "problems": problems,
        "checked_at": db.iso(now),
        "last_success_at": success["finished_at"] if success else None,
        "last_success_run_id": success["id"] if success else None,
        "last_run": {k: last_run.get(k) for k in ("id", "status", "started_at", "finished_at", "collected",
                                                  "source_total", "note")} if last_run else None,
        "active_offers": active,
    }


def create_app(settings: Settings, conn_factory: Callable[[], sqlite3.Connection] | None = None,
               clock: Callable[[], datetime] = db.utcnow) -> FastAPI:
    factory = conn_factory or (lambda: db.connect(settings.db_path))
    app = FastAPI(title="Киравто: склад РОЛЬФ Ясенево", version="1.0", description=DESCRIPTION,
                  openapi_tags=TAGS)

    # ------------------------------------------------------------ ошибки
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        code = ERROR_CODES.get(exc.status_code, f"http_{exc.status_code}")
        msg = ERROR_MESSAGES.get(exc.status_code, str(exc.detail))
        if exc.status_code == 404:
            msg = f"Адрес {request.url.path} не найден"
        return error_response(exc.status_code, code, msg, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        details = [{"where": ".".join(str(p) for p in e.get("loc", ())), "problem": e.get("msg"),
                    "input": e.get("input")} for e in exc.errors()]
        return error_response(422, "validation_error", "Неверные параметры запроса", details)

    @app.exception_handler(Exception)
    async def _internal_error(request: Request, exc: Exception):
        log.exception("ошибка в %s", request.url.path)
        return error_response(500, "internal_error", "Внутренняя ошибка сервера",
                              {"type": exc.__class__.__name__})

    # ------------------------------------------------------- зависимости
    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = factory()
        try:
            yield conn
        finally:
            conn.close()

    bearer = HTTPBearer(auto_error=False, description="Authorization: Bearer <токен>")
    header_key = APIKeyHeader(name="X-API-Token", auto_error=False, description="Токен в заголовке X-API-Token")

    def require_token(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                      x_token: str | None = Depends(header_key)) -> None:
        expected = settings.api_token or ""
        if not expected:
            raise ApiError(503, "token_not_configured",
                           "API_TOKEN не задан: доступ к API закрыт, пока токен не задан в .env")
        given = (cred.credentials if cred else None) or x_token
        if not given:
            raise ApiError(401, "unauthorized",
                           "Нужен токен: заголовок Authorization: Bearer <токен> или X-API-Token",
                           headers={"WWW-Authenticate": "Bearer"})
        if not secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            raise ApiError(401, "unauthorized", "Неверный токен", headers={"WWW-Authenticate": "Bearer"})

    auth = [Depends(require_token)]

    # ------------------------------------------------------------- ручки
    @app.get("/health", tags=["служебное"], summary="Состояние сервиса (без токена)",
             description="Всегда HTTP 200, состояние — в поле `status`:\n"
                         "* `ok` — последний полный прогон свежий;\n"
                         "* `stale` — последний ПОЛНЫЙ прогон старше 3 × RUN_INTERVAL_MIN;\n"
                         "* `degraded` — два последних завершённых прогона неполные/упали, "
                         "но полный прогон ещё свежий;\n"
                         "* `no_data` — не было ни одного полного прогона.\n\n"
                         "`last_success_at` — время окончания последнего полного прогона, "
                         "`last_run` — последний прогон любого статуса.")
    def health(conn: sqlite3.Connection = Depends(get_conn)):
        return health_status(conn, settings, clock())

    @app.get("/offers", tags=["объявления"], dependencies=auth, responses=AUTH_RESPONSES,
             summary="Поиск машин на складе",
             description="Фильтры складываются через И. Марка понимает справочник: «Мерседес», «MB», "
                         "«mercedes-benz» → MERCEDES. В ответе `filters.mark_code` — во что превратилась марка.")
    def offers(
        conn: sqlite3.Connection = Depends(get_conn),
        mark: str | None = Query(None, max_length=60, description="Марка: «Мерседес», «MB», «BMW», «тойота»…"),
        model: str | None = Query(None, max_length=60, description="Модель: «Camry», «Land Cruiser Prado»"),
        section: str | None = Query(None, pattern="^(new|used)$", description="new — новые, used — с пробегом"),
        price_min: int | None = Query(None, ge=0, description="Цена от, ₽"),
        price_max: int | None = Query(None, ge=0, description="Цена до, ₽"),
        year_min: int | None = Query(None, ge=1900, le=2100, description="Год выпуска от"),
        year_max: int | None = Query(None, ge=1900, le=2100, description="Год выпуска до"),
        availability: str | None = Query(None, max_length=30,
                                         description="Наличие: in_stock / IN_STOCK / «в наличии», "
                                                     "in_transit / «в пути», on_order / «под заказ»"),
        status: str = Query("active", pattern="^(active|removed|sold|all)$",
                            description="active — в продаже (по умолчанию), removed — снято, "
                                        "sold — вероятно продано, all — все"),
        sort: str = Query("price", pattern="^(" + "|".join(queries.SORTS) + ")$",
                          description="Сортировка: price, -price, year, -year, new (сначала новые), mileage"),
        limit: int = Query(50, ge=1, le=200, description="Сколько записей вернуть (1..200)"),
        offset: int = Query(0, ge=0, description="Сколько записей пропустить"),
    ):
        problems = []
        if price_min is not None and price_max is not None and price_min > price_max:
            problems.append({"where": "query.price_min", "problem": "price_min больше price_max",
                             "input": price_min})
        if year_min is not None and year_max is not None and year_min > year_max:
            problems.append({"where": "query.year_min", "problem": "year_min больше year_max", "input": year_min})
        if problems:
            raise ApiError(422, "validation_error", "Неверные параметры запроса", problems)
        res = queries.search_offers(conn, mark=mark, model=model, section=section, price_min=price_min,
                                    price_max=price_max, year_min=year_min, year_max=year_max,
                                    availability=availability, status=status, sort=sort, limit=limit,
                                    offset=offset)
        res["filters"] = {"mark_code": nz.resolve_mark(mark) if mark else None,
                          "model_code": nz.resolve_model(model) if model else None,
                          "status": status, "sort": sort, "limit": limit, "offset": offset}
        return res

    @app.get("/offers/{offer_id}", tags=["объявления"], dependencies=auth,
             responses={**AUTH_RESPONSES, **NOT_FOUND}, summary="Карточка объявления",
             description="Все поля объявления, фото, аномалии и факты из описания (если были извлечены).")
    def offer(offer_id: str = PathParam(..., max_length=40, description="ID объявления Авто.ру"),
              conn: sqlite3.Connection = Depends(get_conn)):
        o = queries.get_offer(conn, offer_id)
        if not o:
            raise ApiError(404, "not_found", f"Объявление {offer_id} не найдено", {"offer_id": offer_id})
        return o

    @app.get("/offers/{offer_id}/history", tags=["объявления"], dependencies=auth,
             responses={**AUTH_RESPONSES, **NOT_FOUND}, summary="История цены и событий",
             description="Все зафиксированные цены (по прогонам) и события: появление, смена цены, снятие, "
                         "возврат, перевыставление и т. д.")
    def history(offer_id: str = PathParam(..., max_length=40, description="ID объявления Авто.ру"),
                conn: sqlite3.Connection = Depends(get_conn)):
        h = queries.get_history(conn, offer_id)
        if h is None:
            raise ApiError(404, "not_found", f"Объявление {offer_id} не найдено", {"offer_id": offer_id})
        return h

    @app.get("/drops", tags=["аналитика"], dependencies=auth, responses=AUTH_RESPONSES,
             summary="Снижения цен за период",
             description="Первая цена в окне против последней. `changes` — события, из которых посчитано.")
    def drops(conn: sqlite3.Connection = Depends(get_conn),
              days: int = Query(7, ge=1, le=365, description="Окно, дней"),
              min_pct: float = Query(5.0, ge=0, le=100, description="Минимальное снижение, %")):
        items = queries.price_drops(conn, days=days, min_pct=min_pct, now=clock())
        return {"days": days, "min_pct": min_pct, "total": len(items), "items": items}

    @app.get("/stock", tags=["аналитика"], dependencies=auth, responses=AUTH_RESPONSES,
             summary="Сводка склада",
             description="Сколько машин в продаже (по разделам и наличию), снято, вероятно продано, "
                         "новых и изменений цены за 7 дней, цены, топ марок, последний прогон.")
    def stock(conn: sqlite3.Connection = Depends(get_conn)):
        return queries.stock_summary(conn, now=clock())

    @app.get("/runs", tags=["служебное"], dependencies=auth, responses=AUTH_RESPONSES,
             summary="Журнал прогонов сборщика",
             description="Последние прогоны: статус, собрано / заявлено источником, полнота, запросы, ошибки, "
                         "новые, изменения, снятия, длительность, заметка.")
    def runs(conn: sqlite3.Connection = Depends(get_conn),
             limit: int = Query(20, ge=1, le=500, description="Сколько прогонов вернуть")):
        return {"items": queries.last_runs(conn, limit)}

    @app.get("/report", tags=["аналитика"], dependencies=auth,
             responses={**AUTH_RESPONSES, 200: {"content": {"text/html": {}}, "description": "HTML-отчёт"},
                        404: {"description": "Отчёт ещё не сформирован",
                              "content": {"application/json": {"schema": ERROR_SCHEMA}}}},
             summary="HTML-отчёт", description="Последний отчёт, который пересобирается после каждого прогона.")
    def report():
        path = settings.report_path
        if not path.exists():
            raise ApiError(404, "not_found", "Отчёт ещё не сформирован: он появится после первого прогона",
                           {"path": path.name})
        return FileResponse(path, media_type="text/html; charset=utf-8")

    return app


def app_from_env() -> FastAPI:
    return create_app(load_settings())
