"""Посты: окна, дубли, тексты без None — находки аудита 21.10.2026."""
import copy

from kiravto import posts as P
from kiravto.collector import Collector


def run(conn, source, settings, clock):
    return Collector(conn, source, settings, clock=clock).run()


def test_rejected_drop_not_recreated_next_day(conn, source, settings, clock):
    run(conn, source, settings, clock)
    o = source.offers["used"][0]
    o["price_info"]["price"] = int(o["price_info"]["price"] * 0.8)
    clock.advance(hours=1)
    run(conn, source, settings, clock)
    assert len(P.create_drop_posts(conn, 3, now=clock())) == 1
    conn.execute("UPDATE posts SET status='rejected'")
    conn.commit()
    clock.advance(hours=20)                        # другой календарный день, событие ещё в окне 24 ч
    assert P.create_drop_posts(conn, 3, now=clock()) == []


def test_daily_window_starts_at_previous_post(conn, source, settings, clock):
    run(conn, source, settings, clock)             # базовый склад — в пост не идёт
    def add(i):
        car = copy.deepcopy(source.offers["used"][0])
        car["id"], car["hash"] = f"19000000{i:02d}", f"h{i:07d}"
        source.offers["used"].append(car)
    add(1)
    clock.advance(hours=2)
    run(conn, source, settings, clock)
    first = P.create_daily_post(conn, now=clock())
    assert first
    add(2)
    clock.advance(hours=30)                        # следующий пост позже чем через 24 ч после машины №1
    run(conn, source, settings, clock)
    second = P.create_daily_post(conn, now=clock())
    ids = conn.execute("SELECT offer_ids FROM posts WHERE id=?", (second,)).fetchone()[0]
    assert "1900000002" in ids and "1900000001" not in ids   # без потерь и без повторов


def test_drop_text_without_year_has_no_none():
    post = P.drop_post({"mark": "Kia", "model": "Rio", "year": None, "mileage": 1000, "url": "u", "photos": []},
                       1_000_000, 900_000)
    assert "None" not in post["text"]
