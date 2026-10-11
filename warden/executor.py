"""Executor interface. One real implementation (Agentic Wallet CLI) and one fake for tests."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Protocol


class ExecutorError(Exception):
    pass


@dataclass
class Quote:
    from_token: str
    to_token: str
    from_qty: str
    to_amount_human: str | None
    impact_pct: float | None
    fee: str | None
    vendor: str | None
    raw: dict = field(default_factory=dict)


class Executor(Protocol):
    name: str

    def wallet_status(self) -> dict: ...
    def wallet_settings(self) -> dict: ...
    def tx_locked(self, chain_id: str) -> dict: ...
    def balances(self, chain_id: str) -> list[dict]: ...
    def quote(self, from_token: str, to_token: str, from_qty: str, chain_id: str) -> Quote: ...
    def swap(self, from_token: str, to_token: str, from_qty: str, chain_id: str, slippage: str) -> str: ...
    def order(self, order_id: str, from_token: str, to_token: str) -> dict: ...


class BawExecutor:
    """Binance Agentic Wallet CLI (`baw`), JSON mode. Every call has a timeout; stdout is parsed, never trusted blindly."""
    name = "agentic-wallet"

    def __init__(self, binary: str = "baw", timeout_s: int = 60, env: dict | None = None):
        self.binary, self.timeout_s, self.env = binary, timeout_s, env

    def _run(self, *args: str) -> dict:
        cmd = [self.binary, *args, "--json"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s, env=self.env)
        except subprocess.TimeoutExpired as e:
            raise ExecutorError(f"baw {' '.join(args[:2])} timed out after {self.timeout_s}s") from e
        out = (p.stdout or "").strip()
        try:
            body = json.loads(out) if out else {}
        except json.JSONDecodeError as e:
            raise ExecutorError(f"baw {' '.join(args[:2])} returned non-JSON: {out[:200]!r} / {p.stderr[:200]!r}") from e
        if not body.get("success", True):
            raise ExecutorError(f"baw {' '.join(args[:2])}: {body.get('error') or body.get('message') or body}")
        return body.get("data", body)

    def wallet_status(self) -> dict:
        return self._run("wallet", "status")

    def wallet_settings(self) -> dict:
        return self._run("wallet", "settings")

    def tx_locked(self, chain_id: str) -> dict:
        return self._run("wallet", "tx-lock", "--binanceChainId", chain_id)

    def balances(self, chain_id: str) -> list[dict]:
        d = self._run("wallet", "balance", "--binanceChainId", chain_id)
        return d if isinstance(d, list) else (d.get("balances") or d.get("list") or d.get("tokens") or [])

    def quote(self, from_token, to_token, from_qty, chain_id) -> Quote:
        d = self._run("market-order", "quote", "--fromTokenQty", from_qty, "--fromToken", from_token,
                      "--toToken", to_token, "--binanceChainId", chain_id)
        # measured 2026-10-05: {"fromCoinSymbol","fromCoinAmount","toCoinSymbol","toCoinAmount","slippage"}; no impact, fee or route.
        # The gate computes a price deviation against the public mid instead (A7 resolved).
        impact = d.get("priceImpactPercent") or d.get("priceImpact")
        return Quote(from_token, to_token, from_qty,
                     to_amount_human=str(d.get("toCoinAmount") or d.get("toTokenAmountHuman") or d.get("toAmount") or ""),
                     impact_pct=float(impact) if impact not in (None, "") else None,
                     fee=str(d.get("tradeFee") or d.get("fee") or ""), vendor=d.get("vendorName") or d.get("vendor") or "agentic-wallet", raw=d)

    def swap(self, from_token, to_token, from_qty, chain_id, slippage) -> str:
        d = self._run("market-order", "swap", "--fromTokenQty", from_qty, "--fromToken", from_token,
                      "--toToken", to_token, "--binanceChainId", chain_id, "--slippage", slippage)
        oid = d.get("orderId")
        if not oid:
            raise ExecutorError(f"swap returned no orderId: {d}")
        return str(oid)

    def order(self, order_id, from_token, to_token) -> dict:
        d = self._run("market-order", "list", "--orderId", order_id)
        rows = d if isinstance(d, list) else (d.get("list") or d.get("orders") or [])
        if not rows:  # one entrant saw the stored id differ by one (F-039): fall back to the pair listing
            d2 = self._run("market-order", "list", "--fromToken", from_token, "--toToken", to_token)
            rows = d2 if isinstance(d2, list) else (d2.get("list") or d2.get("orders") or [])
            rows = sorted(rows, key=lambda r: str(r.get("createTime") or r.get("orderId") or ""), reverse=True)[:1]
            for r in rows:
                r["matched_by"] = "pair"
        return rows[0] if rows else {}


@dataclass
class FakeExecutor:
    """Scripted responses for tests. Nothing here talks to a network."""
    name: str = "fake"
    status: str = "CONNECTED"
    settings: dict = field(default_factory=lambda: {"sessionExpireTime": "2099-01-01T00:00:00+00:00", "quotaLeft": 50000, "dailyLimit": 50000, "abnormalTxnHandling": "AutoReject", "tradeAllTokens": True})
    locked: bool = False
    held: dict = field(default_factory=dict)        # token address -> qty string
    impact: float | None = 0.01
    to_amount: str = "4.98"
    order_states: list = field(default_factory=lambda: ["PENDING", "FINISHED"])
    calls: list = field(default_factory=list)

    def wallet_status(self): self.calls.append("status"); return {"status": self.status}
    def wallet_settings(self): self.calls.append("settings"); return dict(self.settings)
    def tx_locked(self, chain_id): self.calls.append("lock"); return {"status": "LOCKED" if self.locked else "UNLOCKED"}
    def balances(self, chain_id): self.calls.append("balance"); return [{"contractAddress": a, "amount": q, "symbol": "X"} for a, q in self.held.items()]
    def quote(self, from_token, to_token, from_qty, chain_id):
        self.calls.append("quote")
        return Quote(from_token, to_token, from_qty, self.to_amount, self.impact, "0.02", "LiquidMesh", {"fake": True, "fromCoinAmount": from_qty, "toCoinAmount": self.to_amount})
    def swap(self, from_token, to_token, from_qty, chain_id, slippage):
        self.calls.append("swap"); return "2610070000000001"
    def order(self, order_id, from_token, to_token):
        self.calls.append("order")
        st = self.order_states.pop(0) if len(self.order_states) > 1 else self.order_states[0]
        return {"orderId": order_id, "status": st, "txHash": "0xfake" if st == "FINISHED" else None}
