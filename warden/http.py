"""One HTTP helper for the public endpoints: timeouts, 429 Retry-After, null-data retry."""
import json
import time
import urllib.error
import urllib.request

from .config import CFG

PUBLIC_HEADERS = {"Accept-Encoding": "identity", "User-Agent": "binance-web3/1.1 (Skill)"}


class ApiError(Exception):
    pass


def get_json(url: str, headers: dict | None = None, allow_null_data: bool = False) -> dict:
    http = CFG["http"]
    last_err = None
    for attempt in range(http["max_retries"] + 1):
        req = urllib.request.Request(url, headers=headers or PUBLIC_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=http["read_timeout_s"]) as r:
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After") or http["retry_after_default_s"])
                last_err = ApiError(f"HTTP 429, retrying in {wait} s")
                time.sleep(wait)
                continue
            raise ApiError(f"HTTP {e.code} for {url}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = ApiError(f"network: {e}")
            time.sleep(2)
            continue
        if body.get("data") is None and not allow_null_data:
            # measured 2026-10-05: code 000000 with data null, populated two seconds later
            last_err = ApiError(f"data null (code {body.get('code')})")
            time.sleep(2)
            continue
        return body
    raise last_err or ApiError("unreachable")
