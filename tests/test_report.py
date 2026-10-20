import re
import statistics
from datetime import datetime, timezone

import pytest

from kiravto.collector import Collector
from kiravto.report import exposure, render_report, runs_week, stock_now_and_week_ago

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
NO_EMPTY = re.compile(r"\b(None|nan|NaN|null|undefined)\b")


def run(conn, source, settings, clock):
    return Collector(conn, source, settings, clock=clock).run()


def created(raw) -> datetime:
    return datetime.fromtimestamp(int(raw["additional_info"]["creation_date"]) / 1000, tz=timezone.utc)


@pytest.fixture
def scenario(conn, source, settings, clock):
    """Неделя жизни склада.

    t0: 22 машины, у одной цена 500 ₽ (аномалия), у другой с пробегом пробег 0 (аномалия).
    t0+1ч: две машины сняты (потом станут «вероятно проданы»).
    t0+8д+1ч: снята ещё одна, у одной цена упала на 20%.
    +10 мин: прогон упёрся в капчу — неполный.
    """
    used = source.offers["used"]
    used[0]["price_info"]["price"] = 500
    used[1]["state"]["mileage"] = 0
    run(conn, source, settings, clock)

    gone_early = [source.remove(str(used[5]["id"])), source.remove(str(used[5]["id"]))]
    clock.advance(hours=1)
    assert run(conn, source, settings, clock)["removed_count"] == 2

    clock.advance(days=8)
    gone_late = source.remove(str(used[5]["id"]))
    drop = source.offers["new"][0]
    old_price = drop["price_info"]["price"]
    drop["price_info"]["price"] = int(old_price * 0.8)
    r3 = run(conn, source, settings, clock)
    assert r3["status"] == "complete" and r3["removed_count"] == 1 and r3["sold_count"] == 2
    removed_late_at = clock()

    clock.advance(minutes=10)
    source.block_after = source.requests + 1
    assert run(conn, source, settings, clock)["status"] == "incomplete"
    return {"early": gone_early, "late": gone_late, "late_at": removed_late_at, "drop": drop,
            "old_price": old_price}


def test_numbers_for_crafted_scenario(scenario, conn, settings, clock):
    now = clock()
    st = stock_now_and_week_ago(conn, now)
    assert st["young"] is False
    assert st["active"] == 19
    # неделю назад: две ранние уже сняты, поздняя ещё продавалась
    assert st["week_ago"] == 20

    exp = exposure(conn)
    expected = [(T0.replace(hour=10) - created(o)).total_seconds() / 86400 for o in scenario["early"]]
    expected.append((scenario["late_at"] - created(scenario["late"])).total_seconds() / 86400)
    expected = [max(0.0, d) for d in expected]
    assert exp["n"] == 3 and exp["from_source"] == 3
    assert exp["mean"] == pytest.approx(statistics.fmean(expected))

    rw = runs_week(conn, now)
    assert rw == {"total": 2, "complete": 1, "incomplete": 1, "failed": 0}

    html = render_report(conn, settings.report_path, now=now, settings=settings).read_text(encoding="utf-8")
    for title in ("Склад", "Медианная цена по моделям", "Самые сильные снижения цены за неделю", "Аномалии",
                  "Последние прогоны"):
        assert f"<h2>{title}</h2>" in html
    assert "<b>19</b><span>машин в продаже сейчас" in html
    assert "<b>20</b><span>было неделю назад" in html
    assert "За неделю: -1" in html
    assert "по 3 машинам" in html
    assert f"<b>{statistics.fmean(expected):.1f}".replace(".", ",") in html
    assert "неполных прогонов за 7 дней (1 из 2)" in html and "<b>50,0 %</b>" in html

    # снижение: было → стало, процент, ссылка
    def rub(v):
        return f"{v:,}".replace(",", " ") + " ₽"
    assert rub(scenario["old_price"]) in html and rub(int(scenario["old_price"] * 0.8)) in html
    assert "−20,0 %" in html and scenario["drop"]["url"] in html.replace("&amp;", "&")

    # аномалии: 2 из 19, с подписями по-русски
    assert "доля объявлений с аномалиями (2 из 19)" in html
    assert "цена ≤ 1000 ₽" in html and "нет пробега у машины с пробегом" in html

    # медиана Land Cruiser Prado: 12 490 000 → снизили на 20%, остальные 11 890 000, 11 890 000, 10 400 000
    prado = statistics.median([int(12_490_000 * 0.8), 11_890_000, 11_890_000, 10_400_000])
    assert rub(int(prado)) in html

    assert "полный" in html and "неполный" in html
    assert not NO_EMPTY.search(html), NO_EMPTY.search(html)
    head = html.split("<main>")[0]                       # самодостаточный: в <head> нет внешних ресурсов
    assert "http" not in head
    assert "<link" not in html and "<script" not in html


def test_young_db_says_so(conn, source, settings, clock):
    run(conn, source, settings, clock)
    clock.advance(days=2)
    st = stock_now_and_week_ago(conn, clock())
    assert st["young"] and st["week_ago"] is None
    html = render_report(conn, settings.report_path, now=clock(), settings=settings).read_text(encoding="utf-8")
    assert "база моложе 7 дней" in html
    assert "было неделю назад" not in html
    assert "снятых машин пока нет" in html
    assert "За последние 7 дней снижений цены не было." in html
    assert not NO_EMPTY.search(html)


def test_empty_db_renders(conn, settings):
    html = render_report(conn, settings.report_path, now=T0, settings=settings).read_text(encoding="utf-8")
    assert "Прогонов ещё не было." in html and "ещё не было" in html
    assert not NO_EMPTY.search(html)


def test_incomplete_run_with_no_source_total_shows_dash(conn, source, settings, clock):
    source.block_after = 0                       # первый же запрос — капча
    run(conn, source, settings, clock)
    html = render_report(conn, settings.report_path, now=clock(), settings=settings).read_text(encoding="utf-8")
    assert "неполный" in html and "Последний полный прогон: ещё не было" in html
    assert not NO_EMPTY.search(html)
