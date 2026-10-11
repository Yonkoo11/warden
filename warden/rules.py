"""Create a rule: resolve the token, confirm the print time, compute the scheduled minute."""
from __future__ import annotations

import datetime as dt

from .config import CFG
from . import rwa, earnings
from .db import DB, iso
from .receipt import new_id

E = CFG["earnings"]


def print_time_for(label: str) -> str | None:
    return {"after-hours": E["after_hours_utc"], "pre-market": E["pre_market_utc"]}.get(label)


def arm(db: DB, address: str, token_symbol: str, sell_pct: int, offset_minutes: int,
        print_date: str | None = None, print_time_utc: str | None = None, time_source: str | None = None) -> dict:
    s = CFG["schedule"]
    if sell_pct not in CFG["limits"]["sell_share_steps_pct"]:
        raise ValueError(f"sell_pct must be one of {CFG['limits']['sell_share_steps_pct']}")
    if not (s["min_offset_minutes"] <= offset_minutes <= s["max_offset_minutes"]):
        raise ValueError(f"offset_minutes must be {s['min_offset_minutes']}..{s['max_offset_minutes']}")
    tok = rwa.by_symbol(token_symbol)
    if not tok:
        raise LookupError(f"{token_symbol} is not a BSC stock token in Binance's list")
    prints = [p for p in earnings.upcoming() if token_symbol in p["tokens"]]
    if print_date:
        prints = [p for p in prints if p["date"] == print_date]
    if not prints and not (print_date and print_time_utc and time_source):
        raise LookupError(f"no print found for {token_symbol} in the next {E['lookahead_days']} days; pass --print-date, --print-time-utc and --time-source to set one")
    p = prints[0] if prints else {"date": print_date, "time": "owner", "confirmed": True, "time_source": time_source}
    confirmed = bool(p.get("confirmed"))
    t = print_time_utc or print_time_for(p.get("time"))
    source = time_source or p["time_source"] + (f" (typical {p.get('time')} minute, assumption A5)" if not print_time_utc else "")
    if print_time_utc and time_source:
        confirmed = True
    scheduled = None
    if confirmed and t:
        pdt = dt.datetime.fromisoformat(f"{p['date']}T{t}:00+00:00")
        scheduled = iso(pdt - dt.timedelta(minutes=offset_minutes))
    return db.create_rule({
        "id": new_id(), "address": address, "token_symbol": token_symbol, "token_contract": tok["contractAddress"],
        "sell_pct": sell_pct, "offset_minutes": offset_minutes, "print_date": p["date"], "print_time_utc": t,
        "time_source": source, "confirmed": int(confirmed), "scheduled_utc": scheduled,
        "state": "armed" if (confirmed and scheduled) else "disarmed",
    })
