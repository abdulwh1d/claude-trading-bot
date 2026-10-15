import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

import config
from conftest import make_thesis
from test_loop import NOW, StubBroker, context, hold
from tradebot.models import ClockInfo
from tradebot.overnight import build_request, collect_overnight, load_brief, save_brief
from tradebot.premarket import run_premarket
from tradebot.review import run_review
from tradebot.thesis import ResearchBrief

BRIEF = {"symbol": "AAPL", "what_changed": "Raised guidance.", "material": True, "events": ["2026-03-03 8-K"],
         "risks": [], "sources": ["sec-0000320193-26-000001"]}


def test_batch_request_shape_with_the_real_sdk():
    anthropic = pytest.importorskip("anthropic")
    seen = {}
    reply = json.dumps({"id": "msgbatch_1", "type": "message_batch", "processing_status": "in_progress",
                        "request_counts": {"processing": 1, "succeeded": 0, "errored": 0, "canceled": 0,
                                           "expired": 0},
                        "created_at": "2026-03-03T01:00:00Z", "expires_at": "2026-03-04T01:00:00Z",
                        "ended_at": None, "archived_at": None, "cancel_initiated_at": None,
                        "results_url": None}).encode()

    class FakeAPI(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = anthropic.Anthropic(api_key="test", base_url=f"http://127.0.0.1:{server.server_port}",
                                     max_retries=0)
        batch = client.messages.batches.create(
            requests=[build_request("AAPL", "2026-03-04", [("news-1", "Apple raises guidance")])])
    finally:
        server.shutdown()
    assert batch.id == "msgbatch_1" and seen["path"] == "/v1/messages/batches"
    req = seen["body"]["requests"][0]
    assert req["custom_id"] == "brief-AAPL-2026-03-04"
    assert req["params"]["model"] == "claude-sonnet-5-5"
    fmt = req["params"]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and set(fmt["schema"]["properties"]) == set(ResearchBrief.model_fields)
    assert "<pasted_content" in req["params"]["messages"][0]["content"]


def test_collect_overnight_saves_briefs_at_batch_prices():
    text = SimpleNamespace(type="text", text=json.dumps(BRIEF))
    message = SimpleNamespace(content=[SimpleNamespace(type="thinking"), text], stop_reason="end_turn",
                              usage=SimpleNamespace(input_tokens=100_000, output_tokens=2_000))
    entry = SimpleNamespace(custom_id="brief-AAPL-2026-03-04", result=SimpleNamespace(type="succeeded", message=message))
    batches = SimpleNamespace(retrieve=lambda bid: SimpleNamespace(processing_status="ended"),
                              results=lambda bid: iter([entry]))
    ctx = context(StubBroker(), hold)
    ctx.client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    ctx.state.set("overnight", {"session": "2026-03-04", "batch_id": "b1", "ids": {"brief-AAPL-2026-03-04": "AAPL"},
                                "collected": False})
    assert collect_overnight(ctx)
    assert load_brief("AAPL", "2026-03-04").material is True
    # Sonnet 5.5 is $2 in / $10 out per million tokens; the Batch API halves it.
    assert ctx.ledger.total() == pytest.approx((100_000 * 2 + 2_000 * 10) / 1e6 * 0.5)


def test_review_without_an_api_key_still_writes_reports():
    ctx = context(StubBroker(), hold)
    ctx.journal.decision(symbol="AAPL", thesis=make_thesis(action="hold"), approved=False, qty=0,
                         reason="Model recommends no trade.", when=NOW)
    run_review(ctx, str(NOW.date()))
    assert (config.REPORTS_DIR / "scorecard.md").exists()
    lines = (config.REPORTS_DIR / "daily_summary.csv").read_text().splitlines()
    assert lines[0].startswith("date,equity") and lines[1].startswith(str(NOW.date()))


def test_premarket_flags_material_news_and_writes_candidates():
    broker = StubBroker()
    ctx = context(broker, hold)
    opens = NOW + timedelta(hours=1)
    save_brief(ResearchBrief(**BRIEF), str(opens.date()))
    run_premarket(ctx, ClockInfo(NOW, False, opens, opens + timedelta(hours=6, minutes=30)))
    assert ctx.state.get("force_analyze") == ["AAPL"]
    rows = ctx.journal.decisions()
    assert [r["phase"] for r in rows] == ["premarket"] and rows[0]["symbol"] == "AAPL" and not rows[0]["approved"]
    assert "FLAGGED" in (config.REPORTS_DIR / f"premarket_{opens.date()}.md").read_text()


def test_off_hours_schedule_and_failure_isolation(monkeypatch):
    from datetime import datetime, timezone

    from tradebot.loop import run_cycle

    opens = datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)          # 9:30 New York time
    broker = StubBroker()
    broker.now = opens - timedelta(hours=9)                             # inside the overnight window
    broker.clock = lambda: ClockInfo(broker.now, False, opens, opens + timedelta(hours=6, minutes=30))
    ctx = context(broker, hold)
    created = []
    ctx.client = SimpleNamespace(messages=SimpleNamespace(batches=SimpleNamespace(
        create=lambda requests: created.append(requests) or SimpleNamespace(id="b1"))))
    ctx.state.set("last_session_seen", "2026-03-03")

    def broken_review(ctx, session):
        raise RuntimeError("boom")

    monkeypatch.setattr("tradebot.loop.run_review", broken_review)
    assert run_cycle(ctx)
    assert ctx.state.get("review_done") == "2026-03-03"                 # a failed review doesn't block the rest
    assert "review_error" in {e["event"] for e in ctx.journal.events()}
    assert ctx.state.get("overnight_session") == "2026-03-04"
    assert created == []                                                # no headlines or filings: nothing to pay for
    assert load_brief("AAPL", "2026-03-04").what_changed.startswith("Nothing material")
    assert ctx.state.get("premarket_done") is None                      # too early for the pre-market brief
    broker.now = opens - timedelta(minutes=59)
    assert run_cycle(ctx)
    assert ctx.state.get("premarket_done") == "2026-03-04"
    assert (config.REPORTS_DIR / "premarket_2026-03-04.md").exists()
