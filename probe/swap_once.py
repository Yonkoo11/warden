#!/usr/bin/env python3
"""Task 1 probe (assumptions A1, A2, A7-A10): buy a small amount of a stock token through the paired
Agentic Wallet, poll to a terminal state, then sell half back. Records field shapes and outcomes to
probe/swaps.jsonl. Real money, capped by --usdt (default 5; Ondo buys under about 4.75 USD are refused, sells under 1 USD). Never prints secrets; baw holds the session.

usage: python3 probe/swap_once.py --token APLDon [--usdt 2] [--skip-sell]
"""
import argparse, json, subprocess, sys, time, datetime, pathlib
from decimal import Decimal, ROUND_DOWN

USDT = "0x55d398326f99059fF775485246999027B3197955"
CHAIN = "56"
OUT = pathlib.Path(__file__).with_name("swaps.jsonl")
TOKENS = {"APLDon": "0x18de24acb876c0b8392d9c55583bb21c0355980b", "PEPon": "0xf99f8f3a95257d82006183bd524efa7aacc9ef7a",
          "NVDAB": "0x02fca66c1d1afb4e2a7884261eb00f63598a7436"}


def baw(*args):
    p = subprocess.run(["baw", *args, "--json"], capture_output=True, text=True, timeout=90)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"success": False, "nonjson": p.stdout[:400], "stderr": p.stderr[:400]}


def log(row):
    row["at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with OUT.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")
    print(json.dumps(row, default=str)[:600])


def poll(order_id, frm, to, seconds=120):
    t0 = time.time()
    while time.time() - t0 < seconds:
        r = baw("market-order", "list", "--orderId", order_id)
        rows = r.get("data") if isinstance(r.get("data"), list) else (r.get("data") or {}).get("list") or (r.get("data") or {}).get("orders") or []
        matched = "id"
        if not rows:
            r2 = baw("market-order", "list", "--fromToken", frm, "--toToken", to)
            rows = r2.get("data") if isinstance(r2.get("data"), list) else (r2.get("data") or {}).get("list") or (r2.get("data") or {}).get("orders") or []
            matched = "pair"
        if rows:
            o = rows[0]
            st = str(o.get("status") or "").upper()
            if st in ("FINISHED", "FAILED"):
                return {"status": st, "txHash": o.get("txHash"), "matched_by": matched, "raw": o}
        time.sleep(3)
    return {"status": "PENDING_AFTER_%ss" % seconds, "matched_by": None}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--token", default="APLDon"); ap.add_argument("--usdt", default="5"); ap.add_argument("--skip-sell", action="store_true")
    a = ap.parse_args()
    if Decimal(a.usdt) > Decimal("6"):
        sys.exit("cap: the probe never spends more than 6 USDT")
    tok = TOKENS[a.token]
    st = baw("wallet", "status"); se = baw("wallet", "settings"); bal = baw("wallet", "balance", "--binanceChainId", CHAIN)
    sd = se.get("data") or {}
    log({"step": "preflight", "status": (st.get("data") or {}).get("status"), "tradeAllTokens": sd.get("tradeAllTokens"),
         "sessionExpireTime": sd.get("sessionExpireTime"), "quotaLeft": sd.get("quotaLeft"), "balance_keys": sorted((bal.get("data") or [{}])[0].keys()) if bal.get("data") else [], "balance_count": len(bal.get("data") or [])})
    q = baw("market-order", "quote", "--fromTokenQty", a.usdt, "--fromToken", USDT, "--toToken", tok, "--binanceChainId", CHAIN)
    log({"step": "quote_buy", "success": q.get("success"), "data": q.get("data"), "error": q.get("error") or q.get("message")})
    if not q.get("success"):
        sys.exit("quote failed; not swapping")
    s = baw("market-order", "swap", "--fromTokenQty", a.usdt, "--fromToken", USDT, "--toToken", tok, "--binanceChainId", CHAIN, "--slippage", "1")
    log({"step": "swap_buy", "success": s.get("success"), "data": s.get("data"), "error": s.get("error") or s.get("message")})
    oid = str((s.get("data") or {}).get("orderId") or "")
    if not oid:
        sys.exit("buy refused; see swaps.jsonl (A1 false for this wallet, or allowed-list)")
    res = poll(oid, USDT, tok); log({"step": "poll_buy", "orderId": oid, **res})
    if res["status"] != "FINISHED" or a.skip_sell:
        return
    bal = baw("wallet", "balance", "--binanceChainId", CHAIN)
    held = None
    for b in bal.get("data") or []:
        if str(b.get("contractAddress") or b.get("address") or "").lower() == tok.lower():
            held = b
    log({"step": "balance_after_buy", "held": held})
    qty = None
    for k in ("amount", "balance", "quantity", "humanAmount", "free"):
        if held and held.get(k) is not None:
            try:
                qty = Decimal(str(held[k])); break
            except Exception:
                pass
    if qty is None:
        log({"step": "sell", "skipped": "could not read held quantity (A8)"}); return
    half = (qty / 2).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    qs = baw("market-order", "quote", "--fromTokenQty", str(half), "--fromToken", tok, "--toToken", USDT, "--binanceChainId", CHAIN)
    log({"step": "quote_sell", "qty": str(half), "success": qs.get("success"), "data": qs.get("data"), "error": qs.get("error") or qs.get("message")})
    s2 = baw("market-order", "swap", "--fromTokenQty", str(half), "--fromToken", tok, "--toToken", USDT, "--binanceChainId", CHAIN, "--slippage", "1")
    log({"step": "swap_sell", "success": s2.get("success"), "data": s2.get("data"), "error": s2.get("error") or s2.get("message")})
    oid2 = str((s2.get("data") or {}).get("orderId") or "")
    if oid2:
        res2 = poll(oid2, tok, USDT); log({"step": "poll_sell", "orderId": oid2, **res2})


if __name__ == "__main__":
    main()
