"""Read-only BSC reads over a public JSON-RPC: ERC-20 balanceOf for any address, no key.
Used for the Rule screen when the address is not the paired wallet (the wallet's own balance comes from baw)."""
from __future__ import annotations

import json
import urllib.request
from decimal import Decimal

RPCS = ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.binance.org"]


def _call(method: str, params: list, timeout: int = 15) -> object:
    last = None
    for rpc in RPCS:
        try:
            req = urllib.request.Request(rpc, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode())
            if "result" in body:
                return body["result"]
            last = body.get("error")
        except Exception as e:  # try the next RPC; the caller sees the last error
            last = str(e)
    raise RuntimeError(f"all BSC RPCs failed: {last}")


def balance_of(token: str, holder: str, decimals: int = 18) -> Decimal:
    data = "0x70a08231" + holder.lower().replace("0x", "").rjust(64, "0")
    raw = _call("eth_call", [{"to": token, "data": data}, "latest"])
    return Decimal(int(raw, 16)) / (Decimal(10) ** decimals)


def is_address(s: str) -> bool:
    s = (s or "").strip()
    return len(s) == 42 and s.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in s[2:])
