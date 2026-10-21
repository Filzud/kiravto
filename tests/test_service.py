"""Обвязка прогона: оповещение о двух неполных прогонах, посты после полного прогона."""
from kiravto import service
from kiravto.collector import Collector


def test_two_incomplete_runs_alert_once(conn, source, settings, clock):
    Collector(conn, source, settings, clock=clock).run()
    assert service.check_incomplete_alert(conn) is None
    for _ in range(2):
        clock.advance(minutes=45)
        source.block_after = source.requests + 1
        Collector(conn, source, settings, clock=clock).run()
    text = service.check_incomplete_alert(conn)
    assert text and "Два неполных прогона подряд" in text and "блокировка" in text
    # отправка не удалась (бот не настроен) — оповещение не теряется, будет повторено
    assert service.after_run(conn, settings, {"status": "incomplete"})["alert_sent"] is False
    assert service.check_incomplete_alert(conn) is not None
    service.mark_alert_sent(conn)                                 # отправилось — про эту серию больше не пишем
    assert service.check_incomplete_alert(conn) is None


def test_run_once_crash_still_reports(conn, settings, monkeypatch):
    """Любая ошибка прогона (браузер, сеть), а не только смена формата: отчёт и оповещение всё равно делаются."""
    import kiravto.collector as col

    class Boom:
        requests = errors = 0

        def listing(self, *a, **k):
            raise RuntimeError("браузер упал")

        def close(self):
            pass

    monkeypatch.setattr(col, "dhash", lambda b: None)
    s = service.run_once(settings, source=Boom())
    assert s["status"] == "failed" and "браузер упал" in s["error"]
    assert settings.report_path.exists()


def test_after_run_creates_drop_post_and_report(conn, source, settings, clock):
    settings.drop_min_pct = 5
    Collector(conn, source, settings, clock=clock).run()
    source.offers["used"][0]["price_info"]["price"] = int(source.offers["used"][0]["price_info"]["price"] * 0.9)
    clock.advance(hours=1)
    s = Collector(conn, source, settings, clock=clock).run()
    out = service.after_run(conn, settings, s, now=clock())
    assert out["drop_posts"] == 1
    assert settings.report_path.exists()
    # повторный вызов в тот же день не создаёт второй пост о той же машине
    assert service.after_run(conn, settings, s, now=clock())["drop_posts"] == 0
    assert conn.execute("SELECT status FROM posts").fetchone()[0] == "new"   # ждёт модерации


def test_bot_without_token_waits_instead_of_crashloop(settings):
    import threading
    stop = threading.Event()
    t = threading.Thread(target=service.bot_loop, args=(settings, stop), daemon=True)
    t.start()
    t.join(0.5)
    assert t.is_alive()          # не вышел — docker не будет перезапускать его по кругу
    stop.set()
    t.join(2)
    assert not t.is_alive()
