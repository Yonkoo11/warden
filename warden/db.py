"""SQLite: rules and receipts. WAL, one writer, state changes inside BEGIN IMMEDIATE."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .config import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
  id TEXT PRIMARY KEY,
  address TEXT NOT NULL,
  token_symbol TEXT NOT NULL,
  token_contract TEXT NOT NULL,
  sell_pct INTEGER NOT NULL CHECK (sell_pct BETWEEN 10 AND 100),
  offset_minutes INTEGER NOT NULL CHECK (offset_minutes BETWEEN 15 AND 240),
  print_date TEXT NOT NULL,
  print_time_utc TEXT,
  time_source TEXT NOT NULL,
  confirmed INTEGER NOT NULL DEFAULT 0,
  scheduled_utc TEXT,
  state TEXT NOT NULL CHECK (state IN ('disarmed','armed','firing','fired','refused','waited')),
  last_session_check TEXT,
  order_id TEXT,
  created_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
  id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL REFERENCES rules(id),
  json TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_utc TEXT NOT NULL
);
"""

STATES = ("disarmed", "armed", "firing", "fired", "refused", "waited")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or ROOT / "data" / "warden.db")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    # ---- rules
    def create_rule(self, r: dict) -> dict:
        r = dict(r)
        r.setdefault("created_utc", iso(now_utc()))
        r.setdefault("state", "armed" if r.get("confirmed") else "disarmed")
        cols = ",".join(r.keys())
        self.conn.execute(f"INSERT INTO rules ({cols}) VALUES ({','.join('?' for _ in r)})", list(r.values()))
        return self.rule(r["id"])

    def rule(self, rule_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
        return dict(row) if row else None

    def rules(self, state: str | None = None) -> list[dict]:
        q = "SELECT * FROM rules" + (" WHERE state=?" if state else "") + " ORDER BY scheduled_utc"
        return [dict(x) for x in self.conn.execute(q, (state,) if state else ()).fetchall()]

    def due(self, now: dt.datetime, window_minutes: int) -> tuple[list[dict], list[dict]]:
        """Armed rules whose minute has come: (inside the fire window, past it)."""
        inside, missed = [], []
        for r in self.rules("armed"):
            if not r["scheduled_utc"]:
                continue
            sched = dt.datetime.fromisoformat(r["scheduled_utc"])
            if now < sched:
                continue
            (inside if now <= sched + dt.timedelta(minutes=window_minutes) else missed).append(r)
        return inside, missed

    def transition(self, rule_id: str, frm: str, to: str, **fields) -> bool:
        """Atomic state change; returns False if the rule was not in `frm` (another tick got it)."""
        assert to in STATES
        sets = ", ".join(["state=?"] + [f"{k}=?" for k in fields])
        vals = [to, *fields.values(), rule_id, frm]
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(f"UPDATE rules SET {sets} WHERE id=? AND state=?", vals)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return cur.rowcount == 1

    def set_fields(self, rule_id: str, **fields):
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE rules SET {sets} WHERE id=?", [*fields.values(), rule_id])

    # ---- receipts
    def save_receipt(self, receipt: dict):
        self.conn.execute("INSERT OR REPLACE INTO receipts (id, rule_id, json, sha256, created_utc) VALUES (?,?,?,?,?)",
                          (receipt["id"], receipt["rule_id"], json.dumps(receipt, sort_keys=True, default=str),
                           receipt["sha256"], receipt.get("created_utc") or iso(now_utc())))

    def receipt(self, rid: str) -> dict | None:
        row = self.conn.execute("SELECT json FROM receipts WHERE id=?", (rid,)).fetchone()
        return json.loads(row[0]) if row else None

    def receipts(self, rule_id: str | None = None) -> list[dict]:
        q = "SELECT json FROM receipts" + (" WHERE rule_id=?" if rule_id else "") + " ORDER BY created_utc DESC"
        return [json.loads(x[0]) for x in self.conn.execute(q, (rule_id,) if rule_id else ()).fetchall()]
