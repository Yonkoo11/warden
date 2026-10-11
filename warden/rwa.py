"""Binance tokenized-securities public endpoints (no key). Shapes measured live, see probe/."""
from __future__ import annotations

import time
from decimal import Decimal

from .config import CFG
from .http import get_json

BASE = CFG["binance"]["public_base"]
CHAIN = CFG["binance"]["chain_id"]
_token_cache: dict = {"at": 0.0, "rows": []}


def token_list(max_age_s: int = 600) -> list[dict]:
    if time.time() - _token_cache["at"] > max_age_s or not _token_cache["rows"]:
        body = get_json(f"{BASE}/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai")
        _token_cache.update(at=time.time(), rows=body["data"])
    return _token_cache["rows"]


def bsc_tokens() -> list[dict]:
    return [t for t in token_list() if t.get("chainId") == CHAIN]


def by_symbol(symbol: str) -> dict | None:
    for t in bsc_tokens():
        if t.get("symbol") == symbol:
            return t
    return None


def by_ticker(ticker: str) -> list[dict]:
    return [t for t in bsc_tokens() if t.get("ticker") == ticker]


def market_status() -> dict:
    return get_json(f"{BASE}/v1/public/wallet-direct/buw/wallet/market/token/rwa/market/status/ai")["data"]


def asset_status(contract: str) -> dict:
    url = f"{BASE}/v1/public/wallet-direct/buw/wallet/market/token/rwa/asset/market/status/ai?chainId={CHAIN}&contractAddress={contract}"
    return get_json(url)["data"]


def dynamic(contract: str) -> dict:
    url = f"{BASE}/v2/public/wallet-direct/buw/wallet/market/token/rwa/dynamic/ai?chainId={CHAIN}&contractAddress={contract}"
    return get_json(url)["data"]


def klines(contract: str, interval: str = "15m", limit: int = 2) -> list:
    # time ranges are rejected by this endpoint (code 000002, measured 2026-10-05); limit-only.
    url = f"{BASE}/v1/public/wallet-direct/buw/wallet/dex/market/token/kline/ai?chainId={CHAIN}&contractAddress={contract}&interval={interval}&limit={limit}"
    return get_json(url)["data"].get("klineInfos") or []


def status_view(symbol: str) -> dict:
    """The one JSON line the CLI prints: brief section 8."""
    tok = by_symbol(symbol)
    if not tok:
        raise LookupError(f"{symbol} is not a BSC stock token in Binance's list")
    contract = tok["contractAddress"]
    st = asset_status(contract)
    dyn = dynamic(contract)
    ti, si = dyn.get("tokenInfo") or {}, dyn.get("stockInfo") or {}
    mult = Decimal(ti.get("sharesMultiplier") or tok.get("multiplier") or "1")
    price = Decimal(ti["price"]) if ti.get("price") else None
    per_share = (price / mult) if price is not None and mult else None
    stock_price = Decimal(si["price"]) if si.get("price") else None
    gap_pct = None
    if per_share is not None and stock_price:
        gap_pct = float((per_share - stock_price) / stock_price * 100)
    # bStocks return marketStatus null (measured); fall back to the market-wide session
    market_status_s = st.get("marketStatus")
    next_open = st.get("nextOpenTime")
    if market_status_s is None or next_open is None:
        ms = market_status()
        market_status_s = market_status_s or ms.get("marketStatus")
        next_open = next_open or ms.get("nextOpenTime")
    session_ok = bool(st.get("openState")) and st.get("reasonCode") == "TRADING"
    return {
        "symbol": symbol,
        "contract": contract,
        "reasonCode": st.get("reasonCode"),
        "reasonMsg": st.get("reasonMsg"),
        "marketStatus": market_status_s,
        "nextOpenTime": next_open,
        "multiplier": str(mult),
        "per_share": f"{per_share:.6f}" if per_share is not None else None,
        "stock_price": f"{stock_price:.6f}" if stock_price is not None else None,
        # measured 2026-10-05: stockInfo carries no timestamp; the session is the only clock Binance gives
        "exchange_session": market_status_s,
        "gap_pct": round(gap_pct, 4) if gap_pct is not None else None,
        "session_ok": session_ok,
    }
