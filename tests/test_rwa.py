"""Offline tests on recorded responses (captured live 2026-10-05). Never edit a fixture to make a test pass."""
import json
from decimal import Decimal
from unittest import mock

from warden import rwa, earnings

TOKENS = [
    {"chainId": "56", "contractAddress": "0x18de24acb876c0b8392d9c55583bb21c0355980b", "symbol": "APLDon", "ticker": "APLD", "type": 1, "multiplier": "1"},
    {"chainId": "56", "contractAddress": "0xf99f8f3a95257d82006183bd524efa7aacc9ef7a", "symbol": "PEPon", "ticker": "PEP", "type": 1, "multiplier": "1.035087254508861537"},
    {"chainId": "56", "contractAddress": "0x02fca66c1d1afb4e2a7884261eb00f63598a7436", "symbol": "NVDAB", "ticker": "NVDA", "type": 3, "multiplier": "1.000778223752807865"},
    {"chainId": "1", "contractAddress": "0x2d1f7226bd1f780af6b9a49dcc0ae00e8df4bdee", "symbol": "NVDAon", "ticker": "NVDA", "type": 1, "multiplier": "1.0017152487959898"},
]
PEP_DYN = {"symbol": "PEPon", "tokenInfo": {"price": "130.264934997841507117", "sharesMultiplier": "1.035087254508861537", "totalHolders": "220"},
           "stockInfo": {"price": "125.92"}, "statusInfo": {"openState": True, "marketStatus": "overnight", "reasonCode": "TRADING", "reasonMsg": None, "nextOpenTime": 1791187260000}}
NVDAB_DYN = {"symbol": "NVDAB", "tokenInfo": {"price": "236.28", "sharesMultiplier": "1.000778223752807865", "totalHolders": None},
             "stockInfo": {"price": None}, "statusInfo": {"openState": True, "marketStatus": None, "reasonCode": "TRADING", "reasonMsg": None, "nextOpenTime": None}}
LIMITED = {"openState": False, "marketStatus": "pause", "reasonCode": "ASSET_LIMITED", "reasonMsg": "earnings", "nextOpenTime": 1791187260000, "nextCloseTime": None}
MARKET = {"marketStatus": "overnight", "openState": True, "nextOpenTime": 1791187260000}


def test_pep_gap_is_not_the_multiplier():
    """PEPon's raw token price sits 3.5% above the stock: that is the multiplier, not a premium."""
    with mock.patch.object(rwa, "token_list", return_value=TOKENS), \
         mock.patch.object(rwa, "asset_status", return_value=PEP_DYN["statusInfo"]), \
         mock.patch.object(rwa, "dynamic", return_value=PEP_DYN):
        v = rwa.status_view("PEPon")
    raw_gap = (Decimal(PEP_DYN["tokenInfo"]["price"]) - Decimal("125.92")) / Decimal("125.92") * 100
    assert raw_gap > 3
    assert abs(v["gap_pct"]) < 0.1
    assert v["session_ok"] is True and v["reasonCode"] == "TRADING"


def test_bstock_null_fields_fall_back_to_market_session():
    with mock.patch.object(rwa, "token_list", return_value=TOKENS), \
         mock.patch.object(rwa, "asset_status", return_value=NVDAB_DYN["statusInfo"]), \
         mock.patch.object(rwa, "dynamic", return_value=NVDAB_DYN), \
         mock.patch.object(rwa, "market_status", return_value=MARKET):
        v = rwa.status_view("NVDAB")
    assert v["marketStatus"] == "overnight" and v["nextOpenTime"] == 1791187260000
    assert v["stock_price"] is None and v["gap_pct"] is None
    expected = Decimal(NVDAB_DYN["tokenInfo"]["price"]) / Decimal(NVDAB_DYN["tokenInfo"]["sharesMultiplier"])
    assert v["per_share"] == f"{expected:.6f}"


def test_asset_limited_earnings_is_not_session_ok():
    with mock.patch.object(rwa, "token_list", return_value=TOKENS), \
         mock.patch.object(rwa, "asset_status", return_value=LIMITED), \
         mock.patch.object(rwa, "dynamic", return_value=PEP_DYN):
        v = rwa.status_view("PEPon")
    assert v["session_ok"] is False and v["reasonCode"] == "ASSET_LIMITED" and v["reasonMsg"] == "earnings"


def test_unknown_symbol_raises():
    with mock.patch.object(rwa, "token_list", return_value=TOKENS):
        try:
            rwa.status_view("NOPE")
            assert False, "expected LookupError"
        except LookupError:
            pass


def test_prints_join_and_time_not_supplied_is_unconfirmed():
    import datetime as dt
    rows_by_day = {
        "2026-10-07": [{"symbol": "APLD", "name": "Applied Digital Corporation", "time": "time-after-hours"}, {"symbol": "XYZ", "time": "time-after-hours"}],
        "2026-10-08": [{"symbol": "PEP", "name": "Pepsico, Inc.", "time": "time-not-supplied"}],
    }
    with mock.patch.object(rwa, "token_list", return_value=TOKENS), \
         mock.patch.object(earnings, "calendar", side_effect=lambda d: rows_by_day.get(d.isoformat(), [])):
        out = earnings.upcoming(days=3, today=dt.date(2026, 10, 6))
    assert [r["ticker"] for r in out] == ["APLD", "PEP"]
    assert out[0]["confirmed"] is True and out[0]["time"] == "after-hours" and out[0]["tokens"] == ["APLDon"]
    assert out[1]["confirmed"] is False and out[1]["time"] == "unknown" and out[1]["time_source"] == "nasdaq:time-not-supplied"
