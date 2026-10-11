"""The clock: 15 s tick, session checks at T-1440 and T-60, fire inside the window, recover on restart."""
from __future__ import annotations

import datetime as dt
import logging
import time

from .config import CFG
from .db import DB, now_utc, iso
from .executor import Executor
from . import gate, receipt as rc

log = logging.getLogger("warden.scheduler")
SCHED = CFG["schedule"]
WINDOW = CFG["earnings"]["fire_window_minutes"]
SESSION_MARKS = sorted(SCHED["session_check_minutes"], reverse=True)


def rule_text(r: dict) -> str:
    return f"Sell {r['sell_pct']}% of {r['token_symbol']} {r['offset_minutes']} minutes before the earnings report."


def build_receipt(r: dict, outcome: gate.Outcome, decision_override: str | None = None, note: str | None = None) -> dict:
    body = {
        "id": rc.new_id(), "rule_id": r["id"], "rule_text": rule_text(r),
        "agent": {"name": "Warden", "agent_id": CFG.get("agent", {}).get("erc8004_id")}, "acting_for": r["address"],
        "token": {"symbol": r["token_symbol"], "chain_id": CFG["binance"]["chain_id"], "contract": r["token_contract"]},
        "print": {"date": r["print_date"], "time_utc": r["print_time_utc"], "time_source": r["time_source"], "confirmed": bool(r["confirmed"])},
        "scheduled_utc": r["scheduled_utc"], "created_utc": iso(now_utc()),
        **outcome.as_dict(),
    }
    if decision_override:
        body["decision"] = decision_override
    if note:
        body["note"] = note
    return rc.finalize(body)


def record(db: DB, r: dict, outcome: gate.Outcome, **kw) -> dict:
    rec = build_receipt(r, outcome, **kw)
    db.save_receipt(rec)
    rc.write_file(rec)
    return rec


def recover(db: DB, ex: Executor) -> list[dict]:
    """Rules left in `firing` by a crash: find their order before any new tick."""
    out = []
    for r in db.rules("firing"):
        o = {}
        if r.get("order_id"):
            try:
                o = ex.order(r["order_id"], r["token_contract"], CFG["binance"]["usdt"])
            except Exception as e:  # recorded, not hidden
                o = {"error": str(e)}
        st = str(o.get("status") or "").upper()
        outcome = gate.Outcome("executed" if st else "waited", None if st == "FINISHED" else ("ORDER_FAILED" if st == "FAILED" else "RECOVERED_UNKNOWN"),
                               [], {"order_id": r.get("order_id"), "terminal_status": st or None, "tx_hash": o.get("txHash")}, ex.name)
        db.transition(r["id"], "firing", "fired" if st else "waited")
        out.append(record(db, r, outcome, note="recovered after restart"))
    return out


def tick(db: DB, ex: Executor, now: dt.datetime | None = None, status_fn=None, poll_sleep=None) -> list[dict]:
    now = now or now_utc()
    receipts = []
    # session checks ahead of the print
    for r in db.rules("armed"):
        sched = dt.datetime.fromisoformat(r["scheduled_utc"]) if r["scheduled_utc"] else None
        if not sched:
            continue
        mins_left = (sched - now).total_seconds() / 60
        passed = [m for m in SESSION_MARKS if mins_left <= m]
        if not passed:
            continue
        mark = min(passed)  # the nearest mark we have reached; one check per mark
        done = r.get("last_session_check") or ""
        if done == f"T-{mark}":
            continue
        chk = gate.session_check(ex, now, min_minutes_left=int(mins_left) if mins_left > 0 else 2)
        db.set_fields(r["id"], last_session_check=f"T-{mark}")
        log.info("session check T-%s rule=%s ok=%s", mark, r["id"], chk.ok)
        if not chk.ok:
            outcome = gate.Outcome("waited", "SESSION_EXPIRED", [chk], executor=ex.name)
            receipts.append(record(db, r, outcome, decision_override="needs re-pair", note=f"session check at T-{mark} minutes"))
    inside, missed = db.due(now, WINDOW)
    for r in missed:
        if db.transition(r["id"], "armed", "waited"):
            receipts.append(record(db, r, gate.Outcome("waited", "MISSED_WINDOW", [], executor=ex.name)))
    for r in inside:
        if not db.transition(r["id"], "armed", "firing"):
            continue  # another tick took it
        kw = {}
        if status_fn: kw["status_fn"] = status_fn
        if poll_sleep: kw["poll_sleep"] = poll_sleep
        outcome = gate.fire(ex, r, now=now, **kw)
        final = {"executed": "fired", "pending": "fired", "refused": "refused", "waited": "waited"}[outcome.decision]
        db.transition(r["id"], "firing", final, order_id=(outcome.order or {}).get("order_id"))
        receipts.append(record(db, r, outcome))
    return receipts


def run(db: DB, ex: Executor, stop_after: int | None = None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    recover(db, ex)
    n = 0
    while stop_after is None or n < stop_after:
        try:
            for rec in tick(db, ex):
                log.info("receipt %s rule=%s decision=%s reason=%s", rec["id"], rec["rule_id"], rec["decision"], rec["reason_code"])
        except Exception:  # the clock must not die; the next tick records the error path
            log.exception("tick failed")
        n += 1
        time.sleep(SCHED["tick_seconds"])
