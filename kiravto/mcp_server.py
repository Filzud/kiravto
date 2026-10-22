"""MCP-сервер Киравто: ИИ-агент отвечает на вопросы о складе по тем же данным, что API и бот.

Только чтение + «предложить пост» (кладёт в модерацию). Инструмента «опубликовать» нет и не будет:
всё, что уходит в канал, подтверждает человек кнопкой в админ-чате.

Запуск: `python -m kiravto.mcp_server` (stdio). Путь к базе — переменная DB_PATH или data/kiravto.db.
"""
from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer

from . import posts as P
from . import queries as Q
from .config import load_settings
from .db import connect, row_to_offer, utcnow

mcp = MCPServer("kiravto", instructions=(
    "Данные склада автосалона РОЛЬФ Ясенево с Авто.ру: машины, цены, история изменений, прогоны сборщика. "
    "Отвечая о ценах и снижениях, всегда смотри get_runs_status: данные верны на момент последнего полного прогона. "
    "Публиковать ты не можешь — только предложить пост (propose_post), его одобрит человек."))

_settings = None


def _conn():
    global _settings
    _settings = _settings or load_settings()
    return connect(_settings.db_path)


def _provenance(conn) -> dict:
    ok = Q.last_success(conn)
    last = Q.last_runs(conn, 1)
    return {
        "источник": "внутренний JSON Авто.ру (тот же, что получает сайт), собран сервисом Киравто",
        "последний_полный_прогон": ok and {"id": ok["id"], "завершён": ok["finished_at"], "собрано": ok["collected"],
                                           "заявлено_источником": ok["source_total"]},
        "последний_прогон": last and {"id": last[0]["id"], "статус": last[0]["status"],
                                      "завершён": last[0]["finished_at"]},
    }


@mcp.tool()
def search_offers(mark: str | None = None, model: str | None = None,
                  section: Literal["new", "used"] | None = None,
                  price_min: int | None = None, price_max: int | None = None,
                  year_min: int | None = None, year_max: int | None = None,
                  availability: str | None = None,
                  status: Literal["active", "removed", "sold", "all"] = "active",
                  sort: Literal["price", "-price", "year", "-year", "new", "mileage"] = "price",
                  limit: int = 20) -> dict:
    """Поиск машин на складе салона. Звать, когда спрашивают «какие есть …», «сколько стоит …», «найди …».
    Марку можно писать как угодно («Мерседес», «MB», «mercedes») — приводится к справочнику.
    section: new — новые, used — с пробегом. availability: «в наличии» / «в пути» / «под заказ».
    status: active — сейчас в продаже (по умолчанию), removed — сняты, sold — вероятно проданы.
    Возвращает total и до limit кратких карточек (id, ссылка, цена, цена со скидками, год, пробег)."""
    conn = _conn()
    try:
        res = Q.search_offers(conn, mark=mark, model=model, section=section, price_min=price_min,
                              price_max=price_max, year_min=year_min, year_max=year_max,
                              availability=availability, status=status, sort=sort, limit=min(limit, 100))
        res["данные"] = _provenance(conn)
        return res
    finally:
        conn.close()


@mcp.tool()
def get_offer(offer_id: str) -> dict:
    """Полная карточка машины по ID объявления: все поля (комплектация, двигатель, VIN — видимая часть,
    владельцы, ПТС, скидки трейд-ин/кредит/страховка, фото, описание), статус, аномалии и факты из описания
    (каждый с дословной цитатой). Звать, когда нужна подробность про конкретную машину."""
    conn = _conn()
    try:
        o = Q.get_offer(conn, offer_id)
        if not o:
            return {"error": f"объявление {offer_id} не найдено в базе"}
        o["photos"] = o["photos"][:5] + ([f"… ещё {len(o['photos']) - 5}"] if len(o["photos"]) > 5 else [])
        return o
    finally:
        conn.close()


@mcp.tool()
def get_offer_history(offer_id: str) -> dict:
    """История объявления: все цены с датами и события (появилось, изменение цены/скидки/описания/фото,
    снято, вернулось, перевыставлено, вероятно продано), с номером прогона, который это увидел.
    Звать, чтобы объяснить, ПОЧЕМУ мы считаем, что цена снизилась или машина продана."""
    conn = _conn()
    try:
        h = Q.get_history(conn, offer_id)
        return h or {"error": f"объявление {offer_id} не найдено в базе"}
    finally:
        conn.close()


@mcp.tool()
def get_price_drops(days: int = 7, min_pct: float = 5.0) -> dict:
    """Машины, подешевевшие за последние days дней минимум на min_pct процентов: было → стало, процент,
    каждое изменение с датой и номером прогона. Звать на вопросы «что подешевело», «какие скидки за неделю».
    В ответе есть блок «данные» — когда был последний полный прогон; учитывай его, говоря об актуальности."""
    conn = _conn()
    try:
        items = Q.price_drops(conn, days=days, min_pct=min_pct)
        return {"days": days, "min_pct": min_pct, "count": len(items), "items": items[:50],
                "данные": _provenance(conn),
                "как_считается": ("Сравнивается первая цена в окне (из события price_changed) с текущей. "
                                  "Цена берётся из карточки объявления Авто.ру (выдача отстаёт на дни); событие "
                                  "пишется, когда очередное чтение карточки видит другую цену, чем в базе.")}
    finally:
        conn.close()


@mcp.tool()
def get_stock_summary() -> dict:
    """Сводка склада: сколько машин в продаже (новые/с пробегом, в наличии/в пути), сколько снято и
    вероятно продано, новых и изменений цены за 7 дней, аномалий и дублей, мин/медиана/макс цены, топ марок.
    Звать на общие вопросы «что сейчас на складе», «сколько машин»."""
    conn = _conn()
    try:
        return Q.stock_summary(conn)
    finally:
        conn.close()


@mcp.tool()
def get_runs_status(limit: int = 10) -> dict:
    """Статус прогонов сборщика: когда, полный или неполный (и почему), сколько собрано против заявленного
    источником, запросов, ошибок, блокировок, новых/изменений/снятий. Звать, чтобы оценить свежесть и
    надёжность данных, прежде чем делать выводы."""
    conn = _conn()
    try:
        runs = Q.last_runs(conn, min(limit, 50))
        ok = Q.last_success(conn)
        return {"last_success": ok, "runs": runs,
                "incomplete_in_a_row": next((i for i, r in enumerate(runs) if r["status"] == "complete"), len(runs))}
    finally:
        conn.close()


@mcp.tool()
def get_below_market(min_pct: float = 5.0, section: Literal["new", "used"] | None = None, limit: int = 20) -> dict:
    """Машины салона дешевле рынка минимум на min_pct процентов. Рынок — медиана цен таких же машин у других
    продавцов Авто.ру (марка, модель, поколение, год ±1, пробег ±20 %, Москва + 200 км); у каждой машины —
    медиана и число похожих. Звать на вопросы «что выгодно», «что ниже рынка», «какие машины дешевле, чем у других».
    В coverage — у скольких машин оценка уже есть: выборки обновляются постепенно, не все машины оценены."""
    conn = _conn()
    try:
        res = Q.below_market(conn, min_pct=min_pct, section=section, limit=min(limit, 100))
        res["данные"] = _provenance(conn)
        res["как_считается"] = ("market_pct = (цена салона − медиана похожих) / медиана × 100. Меньше 5 похожих — "
                                "оценки нет, а не процент по двум машинам. Машины самого салона в выборку не входят.")
        return res
    finally:
        conn.close()


@mcp.tool()
def get_second_source(offer_id: str | None = None) -> dict:
    """Второй источник — сайт самого салона rolf.ru. Без offer_id: сколько машин на сайте, сколько из них нет
    на Авто.ру, сколько склеено с объявлениями. С offer_id: та же машина на сайте салона — полный VIN
    (Авто.ру показывает только 3 символа), цена на сайте и по какому признаку склеено. Звать на вопросы
    «есть ли эта машина на сайте салона», «какой у неё VIN», «совпадает ли цена»."""
    conn = _conn()
    try:
        if offer_id:
            return {"offer_id": offer_id, "sources": Q.offer_sources(conn, offer_id)}
        return Q.second_source_summary(conn)
    finally:
        conn.close()


@mcp.tool()
def propose_post(offer_id: str, comment: str = "") -> dict:
    """Предложить пост о машине в Telegram. НЕ публикует: пост встаёт в очередь модерации, бот пришлёт его
    в админ-чат с кнопками «Опубликовать» / «Отклонить», и решение принимает человек.
    Если цена недавно снижалась — будет пост «Цена снижена», иначе карточка машины. comment — пояснение для модератора."""
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM offers WHERE id=? AND status='active'", (str(offer_id),)).fetchone()
        if not row:
            return {"queued": False, "error": f"активного объявления {offer_id} нет — предлагать нечего"}
        o = row_to_offer(row)
        drops = [d for d in Q.price_drops(conn, days=7, min_pct=0) if d["id"] == o["id"]]
        if drops:
            payload = P.drop_post(o, drops[0]["old_price"], drops[0]["new_price"])
        else:
            payload = {"text": P.short_card(o), "photos": o["photos"][:1]}
        if comment:
            payload["moderator_note"] = comment[:300]
        day = utcnow().date().isoformat()
        pid = P.queue_post(conn, "manual", f"mcp:{o['id']}:{day}", payload, [o["id"]], source="mcp")
        if pid is None:
            return {"queued": False, "warning": "этот пост уже предложен сегодня и ждёт решения человека"}
        return {"queued": True, "post_id": pid, "status": "new",
                "warning": "Пост НЕ опубликован. Он ушёл на модерацию: опубликует только человек кнопкой в админ-чате.",
                "preview": payload["text"]}
    finally:
        conn.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
