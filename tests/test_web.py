"""Web routes on the fake executor with the status reads patched: no network."""
import os
os.environ.setdefault("WARDEN_EXECUTOR", "fake")
os.environ.setdefault("ADMIN_TOKEN", "t")
from unittest import mock
from starlette.testclient import TestClient
from warden import web, rwa, earnings

CONTRACT = "0x18de24acb876c0b8392d9c55583bb21c0355980b"
PRINTS = [{"ticker": "APLD", "company": "Applied Digital Corporation", "date": "2026-10-07", "time": "after-hours", "confirmed": True, "time_source": "nasdaq:time-after-hours", "tokens": ["APLDon"]}]
TOKENS = [{"chainId": "56", "contractAddress": CONTRACT, "symbol": "APLDon", "ticker": "APLD", "type": 1, "multiplier": "1"}]


def client(tmp_path):
    web.DATA_DIR = tmp_path; web.DB_PATH = tmp_path / "t.db"; web.ADMIN_TOKEN = "t"; web.EXECUTOR_NAME = "fake"
    return TestClient(web.app)


def test_rule_page_empty_state(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200 and "Paste a BSC wallet address to see which of its stock tokens report in the next 14 days." in r.text


def test_rule_page_bad_address(tmp_path):
    r = client(tmp_path).get("/?address=nope")
    assert r.status_code == 200 and "not a BSC address" in r.text


def test_receipt_missing(tmp_path):
    r = client(tmp_path).get("/r/nothere")
    assert r.status_code == 404 and "Receipt not found." in r.text


def test_judge_has_no_arm_button(tmp_path):
    with mock.patch.object(earnings, "upcoming", return_value=PRINTS):
        r = client(tmp_path).get("/judge")
    assert r.status_code == 200 and "Arm rule" not in r.text and "Applied Digital" in r.text


def test_health_shape(tmp_path):
    r = client(tmp_path).get("/health").json()
    assert set(["ok", "scheduler", "last_tick_utc", "wallet_session", "armed_rules", "next_fire_utc"]) <= set(r)


def test_admin_arm_requires_token_and_then_arms(tmp_path):
    c = client(tmp_path)
    with mock.patch.object(rwa, "token_list", return_value=TOKENS), mock.patch.object(earnings, "upcoming", return_value=PRINTS):
        r = c.post("/admin/arm", data={"address": "0x554c6293877c5149f8b26D37c744AC6AF72fBAA5", "token": "APLDon", "sell_pct": "50", "offset": "60"})
        assert r.status_code == 401
        r = c.post("/admin/arm", data={"address": "0x554c6293877c5149f8b26D37c744AC6AF72fBAA5", "token": "APLDon", "sell_pct": "50", "offset": "60", "admin_token": "t"}, follow_redirects=False)
        assert r.status_code == 303
        page = c.get("/?address=0x554c6293877c5149f8b26D37c744AC6AF72fBAA5")
    assert "Armed. Fires 2026-10-07 19:05 UTC." in page.text
    assert "No run yet. This rule fires 2026-10-07 19:05 UTC." in page.text


def test_rule_data_rejects_bad_address(tmp_path):
    assert client(tmp_path).get("/api/rule-data?address=zzz").status_code == 400


def test_admin_token_never_accepted_from_query_string(tmp_path):
    c = client(tmp_path)
    assert c.get("/admin/pair?admin_token=t").status_code == 401
    r = c.post("/admin/login", data={"admin_token": "t"}, follow_redirects=False)
    assert r.status_code == 303 and "warden_admin" in r.cookies
    assert c.get("/admin/pair").status_code == 200
