"""Gate and scheduler on a fake executor and a fake clock. No network."""
import datetime as dt

from warden import gate, scheduler
from warden.db import DB, iso
from warden.executor import FakeExecutor

APLD = "0x18de24acb876c0b8392d9c55583bb21c0355980b"
TRADING = {"symbol": "APLDon", "reasonCode": "TRADING", "reasonMsg": None, "marketStatus": "postmarket", "nextOpenTime": 1791187260000,
           "per_share": "25.85", "multiplier": "1", "stock_price": "25.88", "gap_pct": -0.13, "session_ok": True}
LIMITED = {**TRADING, "reasonCode": "ASSET_LIMITED", "reasonMsg": "earnings", "session_ok": False}
NOW = dt.datetime(2026, 10, 7, 19, 5, tzinfo=dt.timezone.utc)


def rule(db, scheduled=NOW, state="armed"):
    return db.create_rule({"id": "r1", "address": "0xabc", "token_symbol": "APLDon", "token_contract": APLD, "sell_pct": 50,
                           "offset_minutes": 60, "print_date": "2026-10-07", "print_time_utc": "20:05",
                           "time_source": "nasdaq:time-after-hours", "confirmed": 1, "scheduled_utc": iso(scheduled), "state": state})


def test_dry_run_passes_every_check_in_order():
    ex = FakeExecutor(held={APLD: "0.385"})
    out = gate.fire(ex, rule(DB(":memory:")), dry_run=True, now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "dry-run" and out.reason_code is None
    assert [c.name for c in out.checks] == ["session", "lock", "status", "balance", "quote", "quota"]
    assert out.sell_qty == "0.192500" and "swap" not in ex.calls


def test_earnings_restriction_refuses_before_any_quote():
    ex = FakeExecutor(held={APLD: "0.385"})
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: LIMITED)
    assert out.decision == "refused" and out.reason_code == "ASSET_LIMITED:earnings"
    assert "quote" not in ex.calls and "swap" not in ex.calls


def test_expired_session_waits():
    ex = FakeExecutor(held={APLD: "0.385"}, settings={"sessionExpireTime": "2026-10-07T19:06:00+00:00", "quotaLeft": 50000})
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "waited" and out.reason_code == "SESSION_EXPIRED" and ex.calls == ["status", "settings"]


def test_locked_wallet_waits():
    ex = FakeExecutor(held={APLD: "0.385"}, locked=True)
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "waited" and out.reason_code == "WALLET_TX_LOCKED"


def test_impact_over_cap_refuses_when_quote_has_impact():
    ex = FakeExecutor(held={APLD: "0.385"}, impact=0.8)
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "refused" and out.reason_code.startswith("IMPACT_OVER_CAP:0.8")


def test_deviation_over_cap_refuses_when_quote_has_no_impact():
    # 0.1925 APLDon at mid 25.85 = 4.976 USDT; a quote of 4.80 USDT is 3.5% below mid -> refused
    ex = FakeExecutor(held={APLD: "0.385"}, impact=None, to_amount="4.80")
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "refused" and out.reason_code.startswith("PRICE_DEVIATION_OVER_CAP:3.")


def test_deviation_inside_cap_passes_when_quote_has_no_impact():
    ex = FakeExecutor(held={APLD: "0.385"}, impact=None, to_amount="4.93")  # 0.9% below mid
    out = gate.fire(ex, rule(DB(":memory:")), dry_run=True, now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "dry-run"
    q = next(c for c in out.checks if c.name == "quote").value
    assert q["impact_pct"] is None and 0.8 < q["deviation_pct"] < 1.0


def test_no_balance_refuses():
    ex = FakeExecutor(held={})
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "refused" and out.reason_code == "NO_BALANCE"


def test_executes_and_polls_to_finished():
    ex = FakeExecutor(held={APLD: "0.385"})
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING, poll_sleep=lambda s: None)
    assert out.decision == "executed" and out.order["terminal_status"] == "FINISHED" and out.order["tx_hash"] == "0xfake"


def test_scheduler_fires_once_inside_window_and_writes_receipt(tmp_path):
    db = DB(tmp_path / "t.db"); rule(db)
    ex = FakeExecutor(held={APLD: "0.385"})
    recs = scheduler.tick(db, ex, now=NOW + dt.timedelta(minutes=1), status_fn=lambda s: TRADING, poll_sleep=lambda s: None)
    assert len(recs) == 1 and recs[0]["decision"] == "executed" and len(recs[0]["sha256"]) == 64
    assert db.rule("r1")["state"] == "fired"
    again = scheduler.tick(db, ex, now=NOW + dt.timedelta(minutes=2), status_fn=lambda s: TRADING, poll_sleep=lambda s: None)
    assert again == [] and ex.calls.count("swap") == 1


def test_scheduler_marks_missed_window(tmp_path):
    db = DB(tmp_path / "t.db"); rule(db)
    recs = scheduler.tick(db, FakeExecutor(), now=NOW + dt.timedelta(minutes=6), status_fn=lambda s: TRADING)
    assert recs[0]["decision"] == "waited" and recs[0]["reason_code"] == "MISSED_WINDOW" and db.rule("r1")["state"] == "waited"


def test_session_check_at_t_minus_60_then_not_repeated(tmp_path):
    db = DB(tmp_path / "t.db"); rule(db)
    ex = FakeExecutor()
    scheduler.tick(db, ex, now=NOW - dt.timedelta(minutes=50), status_fn=lambda s: TRADING)
    assert db.rule("r1")["last_session_check"] == "T-60" and ex.calls.count("settings") == 1
    scheduler.tick(db, ex, now=NOW - dt.timedelta(minutes=40), status_fn=lambda s: TRADING)
    assert ex.calls.count("settings") == 1


def test_recover_firing_rule_after_restart(tmp_path):
    db = DB(tmp_path / "t.db"); rule(db, state="firing"); db.set_fields("r1", order_id="2610070000000001")
    recs = scheduler.recover(db, FakeExecutor(order_states=["FINISHED"]))
    assert recs[0]["decision"] == "executed" and recs[0]["note"] == "recovered after restart" and db.rule("r1")["state"] == "fired"


def test_transition_is_atomic_against_a_stale_state(tmp_path):
    db = DB(tmp_path / "t.db"); rule(db)
    assert db.transition("r1", "armed", "firing") is True
    assert db.transition("r1", "armed", "firing") is False


def test_sell_below_venue_minimum_refuses():
    ex = FakeExecutor(held={APLD: "0.03"}, impact=None, to_amount="0.38")  # 0.015 APLDon ~ 0.38 USDT < 1 USD
    out = gate.fire(ex, rule(DB(":memory:")), now=NOW, status_fn=lambda s: TRADING)
    assert out.decision == "refused" and out.reason_code.startswith("ORDER_BELOW_MINIMUM")


def test_receipt_names_agent_and_owner_and_survives_db_round_trip(tmp_path):
    db = DB(tmp_path / "t.db"); r = rule(db)
    recs = scheduler.tick(db, FakeExecutor(held={APLD: "0.385"}), now=NOW + dt.timedelta(minutes=1), status_fn=lambda s: TRADING, poll_sleep=lambda s: None)
    assert recs[0]["agent"]["name"] == "Warden" and recs[0]["acting_for"] == r["address"]
    stored = db.receipt(recs[0]["id"])
    assert stored["acting_for"] == r["address"] and stored["sha256"] == recs[0]["sha256"]
