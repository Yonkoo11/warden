"""Earnings dates from the Nasdaq calendar, joined to BSC stock tokens. Binance has no calendar."""
from __future__ import annotations

import datetime as dt

from .config import CFG
from .http import get_json
from . import rwa

NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
TIME_MAP = {"time-pre-market": ("pre-market", True), "time-after-hours": ("after-hours", True),
            "time-not-supplied": ("unknown", False)}


def calendar(day: dt.date) -> list[dict]:
    body = get_json(f"{CFG['earnings']['calendar']}?date={day.isoformat()}", headers=NASDAQ_HEADERS, allow_null_data=True)
    return ((body.get("data") or {}).get("rows")) or []


def upcoming(days: int | None = None, today: dt.date | None = None) -> list[dict]:
    """Prints in the next N days for tickers that exist as BSC tokens, soonest first."""
    days = days or CFG["earnings"]["lookahead_days"]
    today = today or dt.datetime.now(dt.timezone.utc).date()
    by_ticker: dict[str, list[dict]] = {}
    for t in rwa.bsc_tokens():
        by_ticker.setdefault(t["ticker"], []).append(t)
    out = []
    for i in range(days + 1):
        day = today + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        for r in calendar(day):
            toks = by_ticker.get(r.get("symbol"))
            if not toks:
                continue
            label, confirmed = TIME_MAP.get(r.get("time"), ("unknown", False))
            out.append({
                "ticker": r["symbol"], "company": (r.get("name") or "").strip(), "date": day.isoformat(),
                "time": label, "confirmed": confirmed, "time_source": f"nasdaq:{r.get('time')}",
                "tokens": [t["symbol"] for t in toks],
            })
    return out


def for_wallet_tokens(symbols: list[str], **kw) -> list[dict]:
    want = set(symbols)
    return [p for p in upcoming(**kw) if want & set(p["tokens"])]
