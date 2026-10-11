"""One receipt per run, including refusals. Canonical JSON, sha256, file and DB."""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from .config import ROOT

RECEIPT_VERSION = 1


def new_id() -> str:
    return secrets.token_hex(6)


def canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def finalize(receipt: dict) -> dict:
    body = {k: v for k, v in receipt.items() if k != "sha256"}
    body["receipt_version"] = RECEIPT_VERSION
    body["sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    return body


def write_file(receipt: dict, data_dir: Path | None = None) -> Path:
    d = (data_dir or ROOT / "data") / "receipts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{receipt['id']}.json"
    p.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    return p
