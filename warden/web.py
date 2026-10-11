"""Warden web: three screens, a JSON API, admin endpoints, health. Starlette + Jinja2, server-rendered.
Strings and states follow the product brief; every public response is plain words, never a raw code."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from .config import CFG, ROOT
from . import rwa, earnings, chain, gate, scheduler, rules as rules_mod
from .db import DB, now_utc, iso
from .executor import BawExecutor, FakeExecutor

log = logging.getLogger("warden.web")
HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
REASON_WORDS = {
    "SESSION_EXPIRED": "wallet session expired", "SESSION_UNKNOWN": "wallet session unreadable",
    "WALLET_TX_LOCKED": "wallet locked in the app", "LOCK_UNKNOWN": "wallet lock unreadable",
    "ASSET_LIMITED": "issuer limited the token", "ASSET_PAUSED": "issuer paused the token",
    "MARKET_CLOSED": "market closed", "MARKET_PAUSED": "market paused", "MARKET_MAINTENANCE": "market maintenance",
    "STATUS_NOT_TRADING": "token not trading", "STATUS_UNKNOWN": "trading status unreadable",
    "NO_BALANCE": "no tokens to sell", "BALANCE_UNKNOWN": "balance unreadable", "SELL_QTY_ZERO": "amount too small",
    "QUOTE_FAILED": "no price quote", "QUOTE_UNPRICED": "quote had no price", "DAILY_QUOTA_EXCEEDED": "daily limit reached",
    "SWAP_REJECTED": "order refused", "ORDER_FAILED": "order failed", "ORDER_PENDING": "order still pending",
    "MISSED_WINDOW": "missed the time window", "RECOVERED_UNKNOWN": "outcome unknown after restart", "UNSUPPORTED": "token pair not supported",
}


def reason_words(code) -> str:
    if not code:
        return ""
    return REASON_WORDS.get(str(code), str(code).replace("_", " ").lower())


templates.env.filters["reason"] = reason_words


def _when(iso_utc: str | None) -> str:
    """'2026-10-07T19:05:00+00:00' -> 'Tue 7 Oct, 19:05'. Falls back to the raw text."""
    try:
        t = dt.datetime.fromisoformat(iso_utc)
    except (TypeError, ValueError):
        return iso_utc or ""
    return f"{t:%a} {t.day} {t:%b}, {t:%H:%M}"


templates.env.filters["when"] = _when
DATA_DIR = Path(os.environ.get("WARDEN_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "warden.db"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
EXECUTOR_NAME = os.environ.get("WARDEN_EXECUTOR", "agentic-wallet")
RUN_SCHEDULER = os.environ.get("RUN_SCHEDULER", "0") == "1"
PAIRED_ADDRESS = os.environ.get("PAIRED_ADDRESS", "").lower()

STATE = {"started_utc": iso(now_utc()), "last_tick_utc": None, "tick_count": 0, "last_error": None, "pairing": None}


def db() -> DB:
    return DB(DB_PATH)


BAW_BIN = os.environ.get("BAW_BIN", "baw")
_wallet_cache = {"at": 0.0, "status": None}


def executor(timeout_s: int = 60):
    if EXECUTOR_NAME == "fake":
        return FakeExecutor(held={"0x18de24acb876c0b8392d9c55583bb21c0355980b": "0.0964"})
    return BawExecutor(binary=BAW_BIN, timeout_s=timeout_s)


def wallet_session_cached(max_age_s: int = 30) -> str:
    """Health and the limits panel must never hang on the wallet CLI: 10 s timeout, 30 s cache."""
    if time.time() - _wallet_cache["at"] < max_age_s and _wallet_cache["status"] is not None:
        return _wallet_cache["status"]
    try:
        st = executor(timeout_s=10).wallet_status().get("status", "UNKNOWN")
    except Exception as e:
        st = f"error: {str(e)[:80]}"
    _wallet_cache.update(at=time.time(), status=st)
    return st


def fmt_ms(ms) -> str | None:
    if not ms:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


import hmac
import secrets

ADMIN_SESSIONS: set[str] = set()  # cookie ids issued by /admin/login; process-local, lost on restart (by design)


def _token_matches(tok: str | None) -> bool:
    return bool(ADMIN_TOKEN) and tok is not None and hmac.compare_digest(tok, ADMIN_TOKEN)


def admin_ok(request: Request, form: dict | None = None) -> bool:
    """Admin = a valid cookie from /admin/login, an X-Admin-Token header, or the token in a POSTed form.
    Never in the query string: it would land in access logs and browser history."""
    cookie = request.cookies.get("warden_admin")
    if cookie and cookie in ADMIN_SESSIONS:
        return True
    return _token_matches(request.headers.get("x-admin-token")) or _token_matches((form or {}).get("admin_token"))


async def admin_login(request: Request):
    if request.method == "GET":
        return templates.TemplateResponse(request, "login.html", {})
    form = dict(await request.form())
    if not _token_matches(form.get("admin_token")):
        return templates.TemplateResponse(request, "login.html", {"error": "Wrong admin token."}, status_code=401)
    sid = secrets.token_urlsafe(32); ADMIN_SESSIONS.add(sid)
    resp = RedirectResponse("/admin/pair", status_code=303)
    resp.set_cookie("warden_admin", sid, httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=12 * 3600)
    return resp


# ---------------------------------------------------------------- pages
async def rule_page(request: Request):
    address = (request.query_params.get("address") or "").strip()
    d = db()
    receipts = d.receipts()[:20]
    slip, evidence = (receipts[0], False) if receipts else (_first_run(), True)
    ctx = {"address": address, "address_valid": chain.is_address(address) if address else None,
           "rules": [r for r in d.rules() if not address or r["address"].lower() == address.lower()],
           "receipts": receipts, "admin_enabled": admin_ok(request), "cfg": CFG,
           "slip": slip, "slip_is_evidence": evidence, "read_utc": now_utc().strftime("%H:%M:%S")}
    return templates.TemplateResponse(request, "rule.html", ctx)


def _first_run() -> dict | None:
    """Real orders from the live wallet check of 2026-10-05 (probe/swaps.jsonl), labelled as not a rule run."""
    try:
        return json.loads((HERE / "static" / "first-run.json").read_text())
    except (OSError, ValueError):
        return None


async def receipt_page(request: Request):
    rid = request.path_params["rid"]
    rec = db().receipt(rid)
    if not rec:
        return templates.TemplateResponse(request, "receipt.html", { "receipt": None, "rid": rid}, status_code=404)
    rule = db().rule(rec["rule_id"])
    return templates.TemplateResponse(request, "receipt.html", { "receipt": rec, "rule": rule, "rid": rid, "pretty": json.dumps(rec, indent=2, sort_keys=True)})


async def judge_page(request: Request):
    prints = []
    try:
        prints = earnings.upcoming()
    except Exception as e:
        STATE["last_error"] = f"prints: {type(e).__name__}"
    return templates.TemplateResponse(request, "judge.html", { "prints": prints, "rules": db().rules(), "receipts": db().receipts()[:10],
                                                     "fetched_utc": iso(now_utc()), "state": STATE})


# ---------------------------------------------------------------- api
async def api_prints(request: Request):
    try:
        return JSONResponse(earnings.upcoming())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


async def api_status(request: Request):
    token = request.query_params.get("token", "")
    try:
        return JSONResponse(rwa.status_view(token))
    except LookupError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


async def api_rule_data(request: Request):
    """Everything the Rule screen needs for one address: tokens held that report in 14 days, each with status and gap."""
    address = (request.query_params.get("address") or "").strip()
    if not chain.is_address(address):
        return JSONResponse({"error": "not an address"}, status_code=400)
    try:
        prints = earnings.upcoming()
    except Exception as e:
        return JSONResponse({"error": "prints unavailable"}, status_code=502)
    out = []
    seen = set()
    for p in prints:
        for sym in p["tokens"]:
            if sym in seen:
                continue
            seen.add(sym)
            tok = rwa.by_symbol(sym)
            if not tok:
                continue
            try:
                held = chain.balance_of(tok["contractAddress"], address)
            except Exception as e:
                out.append({"symbol": sym, "error": "balance read failed"}); continue
            if held <= 0:
                continue
            row = {"symbol": sym, "contract": tok["contractAddress"], "held": str(held), "print": p}
            try:
                row["status"] = rwa.status_view(sym)
            except Exception as e:
                row["status_error"] = type(e).__name__
            out.append(row)
    return JSONResponse({"address": address, "holdings": out, "read_utc": iso(now_utc())})


async def api_wallet(request: Request):
    """Limits panel: read from the paired wallet. Public, policy fields only."""
    try:
        ex = executor(timeout_s=15)
        st = ex.wallet_status(); se = ex.wallet_settings() if str(st.get("status", "")).upper() == "CONNECTED" else {}
        return JSONResponse({"status": st.get("status"), "dailyLimit": se.get("dailyLimit"), "quotaLeft": se.get("quotaLeft"),
                             "abnormalTxnHandling": se.get("abnormalTxnHandling"), "tradeAllTokens": se.get("tradeAllTokens"),
                             "sessionExpireTime": se.get("sessionExpireTime")})
    except Exception as e:
        return JSONResponse({"status": "UNKNOWN", "error": type(e).__name__}, status_code=502)


async def api_rules(request: Request):
    return JSONResponse(db().rules())


async def api_receipts(request: Request):
    return JSONResponse(db().receipts()[:50])


async def api_receipt(request: Request):
    rec = db().receipt(request.path_params["rid"])
    return JSONResponse(rec) if rec else JSONResponse({"error": "Receipt not found."}, status_code=404)


async def health(request: Request):
    ex_state = wallet_session_cached()
    armed = db().rules("armed")
    nxt = min((r["scheduled_utc"] for r in armed if r["scheduled_utc"]), default=None)
    body = {"ok": True, "started_utc": STATE["started_utc"], "scheduler": RUN_SCHEDULER, "last_tick_utc": STATE["last_tick_utc"],
            "tick_count": STATE["tick_count"], "wallet_session": ex_state, "armed_rules": len(armed), "next_fire_utc": nxt,
            "last_error": STATE["last_error"], "executor": EXECUTOR_NAME,
            "wallet_session_store": "file" if os.environ.get("BINANCE_BAW_DIR") else "keychain"}  # never a filesystem path
    return JSONResponse(body)


# ---------------------------------------------------------------- admin
async def admin_arm(request: Request):
    form = dict(await request.form())
    if not admin_ok(request, form):
        return PlainTextResponse("admin token required", status_code=401)
    try:
        r = rules_mod.arm(db(), form["address"].strip(), form["token"].strip(), int(form.get("sell_pct", 50)), int(form.get("offset", 60)),
                          form.get("print_date") or None, form.get("print_time_utc") or None, form.get("time_source") or None)
    except (LookupError, ValueError) as e:
        return templates.TemplateResponse(request, "rule.html", { "address": form.get("address", ""), "address_valid": True,
                                                        "rules": db().rules(), "receipts": db().receipts()[:20], "admin_enabled": True, "cfg": CFG,
                                                        "arm_error": str(e)}, status_code=400)
    return RedirectResponse(f"/?address={r['address']}&armed={r['id']}", status_code=303)


async def admin_pair(request: Request):
    if not admin_ok(request):
        return PlainTextResponse("admin token required", status_code=401)
    if request.method == "POST":
        try:
            p = subprocess.run([BAW_BIN, "auth", "signin", "--json"], capture_output=True, text=True, timeout=60)
            data = json.loads(p.stdout).get("data", {})
            exp = dt.datetime.fromtimestamp(int(data["expireAt"]) / 1000, dt.timezone.utc) if data.get("expireAt") else None
            STATE["pairing"] = {"pairingCode": data.get("pairingCode"), "urlForWeb": data.get("urlForWeb"), "qrCodeId": data.get("qrCodeId"),
                                "started_utc": iso(now_utc()), "expires_utc": iso(exp) if exp else None,
                                "expires_ms": int(data["expireAt"]) if data.get("expireAt") else None, "verify": "waiting"}
            threading.Thread(target=_verify_pairing, args=(data.get("qrCodeId"),), daemon=True).start()
        except Exception as e:
            STATE["pairing"] = {"error": type(e).__name__}
    return templates.TemplateResponse(request, "pair.html", { "pairing": STATE["pairing"], "token": request.query_params.get("admin_token", "")})


def _verify_pairing(qr_id):
    try:
        p = subprocess.run([BAW_BIN, "auth", "verify", "--qrCodeId", str(qr_id), "--json"], capture_output=True, text=True, timeout=330)
        out = p.stdout or p.stderr
        if '"SUCCESS"' in out:
            STATE["pairing"]["verify"] = "paired"
            wallet_session_cached(max_age_s=0)
        elif "AUTH_REJECTED" in out or "351701" in out:
            STATE["pairing"]["verify"] = "expired"
        else:
            STATE["pairing"]["verify"] = "failed"
            log.warning("pair verify: %s", out[:300])
    except subprocess.TimeoutExpired:
        STATE["pairing"]["verify"] = "expired"
    except Exception as e:
        STATE["pairing"]["verify"] = "failed"
        log.warning("pair verify: %s", type(e).__name__)


# ---------------------------------------------------------------- scheduler thread
def _scheduler_loop():
    d = db(); ex = executor()
    try:
        scheduler.recover(d, ex)
    except Exception as e:
        STATE["last_error"] = f"recover: {e}"
    while True:
        try:
            for rec in scheduler.tick(d, ex):
                log.info("receipt %s rule=%s decision=%s reason=%s", rec["id"], rec["rule_id"], rec["decision"], rec["reason_code"])
            STATE["last_tick_utc"] = iso(now_utc()); STATE["tick_count"] += 1
        except Exception as e:
            STATE["last_error"] = f"tick: {type(e).__name__}"
            log.exception("tick failed")
        time.sleep(CFG["schedule"]["tick_seconds"])


import contextlib


@contextlib.asynccontextmanager
async def lifespan(app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db()
    log.info("executor tier: %s; data dir %s; scheduler %s", EXECUTOR_NAME, DATA_DIR, "on" if RUN_SCHEDULER else "off")
    if RUN_SCHEDULER:
        threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler").start()
        log.info("scheduler started, tick %ss", CFG["schedule"]["tick_seconds"])
    yield


routes = [
    Route("/", rule_page), Route("/r/{rid}", receipt_page), Route("/judge", judge_page), Route("/health", health),
    Route("/api/prints", api_prints), Route("/api/status", api_status), Route("/api/rule-data", api_rule_data), Route("/api/wallet", api_wallet),
    Route("/api/rules", api_rules), Route("/api/receipts", api_receipts), Route("/api/receipts/{rid}", api_receipt),
    Route("/admin/login", admin_login, methods=["GET", "POST"]), Route("/admin/arm", admin_arm, methods=["POST"]), Route("/admin/pair", admin_pair, methods=["GET", "POST"]),
    Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
]
app = Starlette(routes=routes, lifespan=lifespan)
