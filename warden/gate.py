"""The ordered checks. Stops at the first failure. Never raises past fire(); every outcome is a receipt."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from .config import CFG
from .executor import Executor, ExecutorError
from . import rwa
from .db import now_utc, iso

USDT = CFG["binance"]["usdt"]
CHAIN = CFG["binance"]["chain_id"]
LIM = CFG["limits"]
ORD = CFG["orders"]

REFUSE_STATUS = {"ASSET_LIMITED", "ASSET_PAUSED", "MARKET_PAUSED", "MARKET_CLOSED", "UNSUPPORTED", "MARKET_MAINTENANCE"}


@dataclass
class Check:
    name: str
    value: object
    ok: bool
    at: str = field(default_factory=lambda: iso(now_utc()))

    def as_dict(self):
        return {"name": self.name, "value": self.value, "ok": self.ok, "at": self.at}


@dataclass
class Outcome:
    decision: str              # executed | refused | waited | dry-run | pending
    reason_code: str | None
    checks: list[Check]
    order: dict | None = None
    executor: str = ""
    sell_qty: str | None = None

    def as_dict(self):
        return {"decision": self.decision, "reason_code": self.reason_code, "checks": [c.as_dict() for c in self.checks],
                "order": self.order, "executor": self.executor, "sell_qty": self.sell_qty}


def _held_qty(balances: list[dict], contract: str) -> Decimal | None:
    for b in balances:
        addr = str(b.get("contractAddress") or b.get("address") or b.get("tokenAddress") or "").lower()
        if addr == contract.lower():
            q = b.get("amount") or b.get("balance") or b.get("quantity") or b.get("humanAmount")
            if q is not None:
                try:
                    return Decimal(str(q))
                except Exception:
                    return None
    return Decimal("0") if balances else None


def session_check(ex: Executor, now: dt.datetime, min_minutes_left: int = 2) -> Check:
    """Used by the scheduler at T-1440 / T-60 and by fire() as its first check."""
    try:
        st = ex.wallet_status()
        if str(st.get("status", "")).upper() != "CONNECTED":
            return Check("session", {"status": st.get("status")}, False)
        se = ex.wallet_settings()
        exp = se.get("sessionExpireTime")
        ok = True
        if exp:
            try:
                exp_dt = dt.datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                ok = exp_dt - now >= dt.timedelta(minutes=min_minutes_left)
            except ValueError:
                ok = True
        return Check("session", {"status": "CONNECTED", "sessionExpireTime": exp, "tradeAllTokens": se.get("tradeAllTokens"),
                                 "dailyLimit": se.get("dailyLimit"), "quotaLeft": se.get("quotaLeft"),
                                 "abnormalTxnHandling": se.get("abnormalTxnHandling")}, ok)
    except ExecutorError as e:
        return Check("session", {"error": str(e)}, False)


def fire(ex: Executor, rule: dict, dry_run: bool = False, now: dt.datetime | None = None,
         status_fn=rwa.status_view, poll_sleep=None) -> Outcome:
    import time
    now = now or now_utc()
    sleep = poll_sleep or time.sleep
    checks: list[Check] = []
    token = rule["token_contract"]

    def refuse(code):
        return Outcome("refused", code, checks, executor=ex.name)

    def waited(code):
        return Outcome("waited", code, checks, executor=ex.name)

    # 1 session
    s = session_check(ex, now)
    checks.append(s)
    if not s.ok:
        return waited("SESSION_EXPIRED" if "status" in (s.value or {}) else "SESSION_UNKNOWN")
    quota_left = (s.value or {}).get("quotaLeft")

    # 2 lock
    try:
        lk = ex.tx_locked(CHAIN)
        st_lock = str(lk.get("status") or "").upper()
        locked = (st_lock != "UNLOCKED") if st_lock else bool(lk.get("locked") or lk.get("isLocked") or lk.get("txLock"))
        checks.append(Check("lock", "locked" if locked else "unlocked", not locked))
        if locked:
            return waited("WALLET_TX_LOCKED")
    except ExecutorError as e:
        checks.append(Check("lock", {"error": str(e)}, False)); return waited("LOCK_UNKNOWN")

    # 3 issuer status
    try:
        sv = status_fn(rule["token_symbol"])
    except Exception as e:  # network or lookup; fail closed
        checks.append(Check("status", {"error": str(e)[:200]}, False)); return waited("STATUS_UNKNOWN")
    ok = bool(sv.get("session_ok")) and sv.get("reasonCode") == "TRADING"
    checks.append(Check("status", {"reasonCode": sv.get("reasonCode"), "reasonMsg": sv.get("reasonMsg"),
                                   "marketStatus": sv.get("marketStatus"), "nextOpenTime": sv.get("nextOpenTime"),
                                   "per_share": sv.get("per_share"), "stock_price": sv.get("stock_price"), "gap_pct": sv.get("gap_pct")}, ok))
    if not ok:
        code = sv.get("reasonCode") or "STATUS_NOT_TRADING"
        return refuse(f"{code}{(':' + str(sv.get('reasonMsg'))) if sv.get('reasonMsg') else ''}")

    # 4 balance
    try:
        held = _held_qty(ex.balances(CHAIN), token)
    except ExecutorError as e:
        checks.append(Check("balance", {"error": str(e)}, False)); return waited("BALANCE_UNKNOWN")
    if held is None or held <= 0:
        checks.append(Check("balance", {"held": str(held)}, False)); return refuse("NO_BALANCE")
    sell_qty = (held * Decimal(rule["sell_pct"]) / Decimal(100)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    checks.append(Check("balance", {"held": str(held), "sell": str(sell_qty)}, sell_qty > 0))
    if sell_qty <= 0:
        return refuse("SELL_QTY_ZERO")

    # 5 quote
    try:
        q = ex.quote(token, USDT, str(sell_qty), CHAIN)
    except ExecutorError as e:
        checks.append(Check("quote", {"error": str(e)}, False)); return refuse("QUOTE_FAILED")
    notional = None
    try:
        notional = float(q.to_amount_human) if q.to_amount_human else None
    except ValueError:
        notional = None
    # Venue minimum first (Ondo, measured 2026-10-05: sell >= 1 USD, buy >= ~4.75 USD): a too-small order is refused
    # with its number before any price judgement.
    if notional is not None and notional < LIM["min_sell_usd"]:
        checks.append(Check("quote", {"vendor": q.vendor, "to_usdt": q.to_amount_human, "min_sell_usd": LIM["min_sell_usd"]}, False))
        return refuse(f"ORDER_BELOW_MINIMUM:{notional}<{LIM['min_sell_usd']}")
    # The wallet quote carries no impact field (measured 2026-10-05). Measure the all-in price against the
    # public mid (per-token price from Binance), which includes fee and spread; cap from warden.toml.
    mid = None
    try:
        mid = float(sv["per_share"]) * float(sv.get("multiplier") or 1) if sv.get("per_share") else None
    except (TypeError, ValueError):
        mid = None
    implied = (notional / float(sell_qty)) if (notional and float(sell_qty) > 0) else None
    deviation = ((mid - implied) / mid * 100) if (mid and implied) else None
    impact = q.impact_pct
    if impact is not None:
        q_ok = abs(impact) <= LIM["price_impact_cap_pct"]
        bad = f"IMPACT_OVER_CAP:{impact}>{LIM['price_impact_cap_pct']}"
    elif deviation is not None:
        q_ok = deviation <= LIM["price_deviation_cap_pct"]
        bad = f"PRICE_DEVIATION_OVER_CAP:{deviation:.3f}>{LIM['price_deviation_cap_pct']}"
    else:
        q_ok, bad = False, "QUOTE_UNPRICED"
    checks.append(Check("quote", {"vendor": q.vendor, "impact_pct": impact, "mid_usdt": mid, "implied_usdt": implied,
                                  "deviation_pct": round(deviation, 4) if deviation is not None else None,
                                  "fee": q.fee, "to_usdt": q.to_amount_human, "raw_keys": sorted(q.raw.keys())}, q_ok))
    if not q_ok:
        return refuse(bad)

    # 6 quota and notional cap
    n_ok = notional is None or notional <= LIM["max_notional_usd"]
    ql_ok = quota_left is None or notional is None or float(quota_left) >= notional
    checks.append(Check("quota", {"quotaLeft": quota_left, "notional_usd": notional, "max_notional_usd": LIM["max_notional_usd"]}, n_ok and ql_ok))
    if not n_ok:
        return refuse(f"NOTIONAL_OVER_CAP:{notional}>{LIM['max_notional_usd']}")
    if not ql_ok:
        return refuse("DAILY_QUOTA_EXCEEDED")

    if dry_run:
        return Outcome("dry-run", None, checks, executor=ex.name, sell_qty=str(sell_qty))

    # 7 execute, 8 poll
    try:
        oid = ex.swap(token, USDT, str(sell_qty), CHAIN, str(LIM["slippage_pct"]))
    except ExecutorError as e:
        checks.append(Check("swap", {"error": str(e)}, False)); return refuse("SWAP_REJECTED")
    checks.append(Check("swap", {"orderId": oid}, True))
    order = {"order_id": oid, "terminal_status": None, "tx_hash": None}
    deadline = ORD["poll_for_s"]
    waited_s = 0
    while waited_s <= deadline:
        try:
            o = ex.order(oid, token, USDT)
        except ExecutorError as e:
            o = {"error": str(e)}
        st = str(o.get("status") or "").upper()
        if st in ("FINISHED", "FAILED"):
            order.update(terminal_status=st, tx_hash=o.get("txHash"), matched_by=o.get("matched_by", "id"))
            return Outcome("executed", None if st == "FINISHED" else "ORDER_FAILED", checks, order, ex.name, str(sell_qty))
        sleep(ORD["poll_every_s"]); waited_s += ORD["poll_every_s"]
    order["terminal_status"] = "PENDING"
    return Outcome("pending", "ORDER_PENDING", checks, order, ex.name, str(sell_qty))
