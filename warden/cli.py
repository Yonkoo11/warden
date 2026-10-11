"""warden CLI: status | prints | arm | fire | receipts | run | recover."""
import argparse
import json
import sys

from . import rwa, earnings


def _executor(name):
    if name == "fake":
        from .executor import FakeExecutor
        return FakeExecutor(held={"0x18de24acb876c0b8392d9c55583bb21c0355980b": "0.385"})
    from .executor import BawExecutor
    return BawExecutor()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="warden")
    ap.add_argument("--db", default=None, help="SQLite path (default data/warden.db)")
    ap.add_argument("--executor", default="agentic-wallet", choices=["agentic-wallet", "fake"])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status"); s.add_argument("--token", required=True)
    p = sub.add_parser("prints"); p.add_argument("--days", type=int, default=None); p.add_argument("--token", action="append", default=None)
    a = sub.add_parser("arm"); a.add_argument("--address", required=True); a.add_argument("--token", required=True)
    a.add_argument("--sell-pct", type=int, default=50); a.add_argument("--offset", type=int, default=60)
    a.add_argument("--print-date"); a.add_argument("--print-time-utc"); a.add_argument("--time-source")
    f = sub.add_parser("fire"); f.add_argument("--rule", required=True); f.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("receipts"); r.add_argument("--rule", default=None)
    sub.add_parser("rules")
    ru = sub.add_parser("run"); ru.add_argument("--ticks", type=int, default=None)
    sub.add_parser("recover")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "status":
            print(json.dumps(rwa.status_view(args.token))); return 0
        if args.cmd == "prints":
            rows = earnings.for_wallet_tokens(args.token, days=args.days) if args.token else earnings.upcoming(days=args.days)
            for row in rows: print(json.dumps(row))
            if not rows: print("no prints in the window", file=sys.stderr)
            return 0
        from .db import DB
        db = DB(args.db)
        if args.cmd == "arm":
            from .rules import arm
            rule = arm(db, args.address, args.token, args.sell_pct, args.offset, args.print_date, args.print_time_utc, args.time_source)
            print(json.dumps(rule)); return 0
        if args.cmd == "rules":
            for row in db.rules(): print(json.dumps(row))
            return 0
        if args.cmd == "receipts":
            for row in db.receipts(args.rule): print(json.dumps(row))
            return 0
        ex = _executor(args.executor)
        if args.cmd == "fire":
            from . import gate, scheduler
            rule = db.rule(args.rule)
            if not rule: print(f"error: rule {args.rule} not found", file=sys.stderr); return 2
            out = gate.fire(ex, rule, dry_run=args.dry_run)
            if out.decision == "dry-run":
                q = next(c for c in out.checks if c.name == "quote").value
                price = f"quote impact {q['impact_pct']}%" if q.get("impact_pct") is not None else f"quote {q.get('deviation_pct')}% from mid, {q.get('to_usdt','')[:8]} USDT"
                print(f"dry-run: would execute sell {rule['sell_pct']}% {rule['token_symbol']} via {ex.name}; {price}; checks ok"); return 0
            if out.decision in ("executed", "pending") and not args.dry_run:
                db.transition(rule["id"], rule["state"], "fired", order_id=(out.order or {}).get("order_id"))
                rec = scheduler.record(db, rule, out)
                print(f"{out.decision} \u00b7 order {out.order['order_id']} {out.order['terminal_status']} \u00b7 tx {out.order.get('tx_hash')} \u00b7 receipt {rec['id']}"); return 0
            st = next((c for c in out.checks if c.name == "status"), None)
            nxt = (st.value or {}).get("nextOpenTime") if st else None
            if out.decision == "refused":
                if not args.dry_run:
                    db.transition(rule["id"], rule["state"], "refused"); scheduler.record(db, rule, out)
                print(f"Refused \u00b7 {out.reason_code}" + (f" \u00b7 next open {nxt} UTC" if nxt else "")); return 2
            if not args.dry_run:
                db.transition(rule["id"], rule["state"], "waited"); scheduler.record(db, rule, out)
            print(f"Waited \u00b7 {out.reason_code}"); return 3
        if args.cmd == "run":
            from . import scheduler
            scheduler.run(db, ex, stop_after=args.ticks); return 0
        if args.cmd == "recover":
            from . import scheduler
            for rec in scheduler.recover(db, ex): print(json.dumps(rec))
            return 0
        return 2
    except (LookupError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main())
