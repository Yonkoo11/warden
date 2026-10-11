#!/usr/bin/env python3
"""Probe F-081: does a BSC stock token move on-chain in the hours after an earnings print?

Polls the PUBLIC (keyless) Binance tokenized-securities endpoints for the tokens below and
appends one JSON line per token per run to probe/prints.jsonl. No key, no wallet, no secrets.
Windows (UTC): APLDon 2026-10-07 19:30 -> 2026-10-08 03:00 (print after hours ~20:05 UTC),
PEPon 2026-10-08 09:30 -> 2026-10-08 16:00 (print pre-market ~10:00 UTC). Outside a window it
exits 0 at once, so it is safe to run every 5 minutes.
"""
import json, sys, time, datetime, urllib.request, pathlib

BASE = "https://www.binance.com/bapi/defi"
H = {"Accept-Encoding": "identity", "User-Agent": "binance-web3/1.1 (Skill)"}
TOKENS = {
    "APLDon": ("56", None, "2026-10-07T19:30:00+00:00", "2026-10-08T03:00:00+00:00"),
    "PEPon":  ("56", None, "2026-10-08T09:30:00+00:00", "2026-10-08T16:00:00+00:00"),
}
OUT = pathlib.Path(__file__).with_name("prints.jsonl")

def get(url, tries=3):
    """The dynamic endpoint sometimes answers code 000000 with data null; retry before believing it."""
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers=H)
        with urllib.request.urlopen(req, timeout=20) as r:
            last = json.loads(r.read().decode())
        if last.get("data") is not None:
            return last
        time.sleep(2)
    raise ValueError(f"data null after {tries} tries: code={last.get('code')}")

def resolve(symbol):
    data = get(f"{BASE}/v1/public/wallet-direct/buw/wallet/market/token/rwa/stock/detail/list/ai")["data"]
    for x in data:
        if x.get("chainId") == "56" and x.get("symbol") == symbol:
            return x["contractAddress"]
    return None

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    force = "--force" in sys.argv
    for sym, (chain, addr, start, end) in TOKENS.items():
        s = datetime.datetime.fromisoformat(start); e = datetime.datetime.fromisoformat(end)
        if not force and not (s <= now <= e):
            continue
        row = {"at_utc": now.isoformat(timespec="seconds"), "symbol": sym}
        try:
            addr = addr or resolve(sym)
            row["contract"] = addr
            dyn = get(f"{BASE}/v2/public/wallet-direct/buw/wallet/market/token/rwa/dynamic/ai?chainId={chain}&contractAddress={addr}")["data"]
            ti, si, st = dyn.get("tokenInfo", {}), dyn.get("stockInfo", {}), dyn.get("statusInfo", {})
            row.update({"token_price": ti.get("price"), "multiplier": ti.get("sharesMultiplier"),
                        "stock_price": si.get("price"), "status": st})
            kl = get(f"{BASE}/v1/public/wallet-direct/buw/wallet/dex/market/token/kline/ai?chainId={chain}&contractAddress={addr}&interval=15m&limit=2")["data"]
            row["klines_15m_last2"] = kl.get("klineInfos")
            row["result"] = "ok"
        except Exception as ex:  # network or schema; recorded, never hidden
            row["result"] = f"error: {type(ex).__name__}: {str(ex)[:160]}"
        with OUT.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(row["symbol"], row["result"], row.get("token_price"), row.get("stock_price"), (row.get("status") or {}).get("reasonCode"))

if __name__ == "__main__":
    main()
