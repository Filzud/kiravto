"""MCP-сервер через настоящий MCP-клиент (stdio): список инструментов, вызовы, предложение поста."""
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from kiravto.collector import Collector

ROOT = Path(__file__).resolve().parent.parent
READ_TOOLS = {"search_offers", "get_offer", "get_offer_history", "get_price_drops", "get_stock_summary",
              "get_runs_status"}


@pytest.fixture
def filled_db(conn, source, settings, clock):
    Collector(conn, source, settings, clock=clock).run()
    victim = source.offers["used"][2]
    victim["price_info"]["price"] = int(victim["price_info"]["price"] * 0.9)
    clock.advance(hours=1)
    Collector(conn, source, settings, clock=clock).run()
    return settings.db_path, str(victim["id"])


async def call(session, name, **args):
    res = await session.call_tool(name, args)
    assert not res.is_error, res.content
    return json.loads(res.content[0].text)


async def test_mcp_end_to_end(filled_db, conn):
    db, victim_id = filled_db
    params = StdioServerParameters(command=sys.executable, args=["-m", "kiravto.mcp_server"], cwd=str(ROOT),
                                   env={**os.environ, "DB_PATH": str(db), "PYTHONIOENCODING": "utf-8"})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name: t for t in (await s.list_tools()).tools}
            assert READ_TOOLS <= set(tools)
            assert "propose_post" in tools
            # публиковать напрямую нечем
            assert not any("publish" in n or "send" in n for n in tools)
            assert all(len(t.description or "") > 80 for t in tools.values())

            found = await call(s, "search_offers", section="used", limit=5)
            assert found["total"] == 14 and len(found["items"]) == 5
            assert found["данные"]["последний_полный_прогон"]["собрано"] == 22

            drops = await call(s, "get_price_drops", days=7, min_pct=5)
            assert drops["count"] == 1 and drops["items"][0]["id"] == victim_id
            assert drops["items"][0]["drop_pct"] == pytest.approx(10, abs=0.1)

            hist = await call(s, "get_offer_history", offer_id=victim_id)
            assert [e["type"] for e in hist["events"]][:2] == ["appeared", "price_changed"]

            card = await call(s, "get_offer", offer_id=victim_id)
            assert card["id"] == victim_id and card["description"]

            summary = await call(s, "get_stock_summary")
            assert summary["active"] == 22

            runs = await call(s, "get_runs_status")
            assert runs["last_success"]["status"] == "complete" and runs["incomplete_in_a_row"] == 0

            prop = await call(s, "propose_post", offer_id=victim_id, comment="хорошая скидка")
            assert prop["queued"] is True and "НЕ опубликован" in prop["warning"]
            again = await call(s, "propose_post", offer_id=victim_id)
            assert again["queued"] is False

    row = conn.execute("SELECT status, source FROM posts").fetchone()
    assert (row["status"], row["source"]) == ("new", "mcp")     # в модерации, не опубликован
