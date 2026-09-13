import tempfile
from pathlib import Path

from src.options_research import attach_forward_returns, build_snapshots, load_normalized


def test_build_snapshot_derives_mid_and_spread():
    rows = [{
        "observed_at": "2026-09-13T00:00:00Z",
        "timestamp": 1789257600000,
        "underlying": "BTC",
        "instrument": "BTC-TEST-CALL",
        "option_type": "call",
        "strike": 77000,
        "expiry": "1789344000000",
        "bid": "100",
        "ask": "102",
        "last": "101",
        "index_price": "77000",
        "delta": "0.5",
    }]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "normalized.jsonl"
        p.write_text("\n".join(__import__("json").dumps(x) for x in rows) + "\n", encoding="utf-8")
        loaded = load_normalized(p)
    built = build_snapshots(loaded)
    assert built[0]["mid"] == 101
    assert built[0]["spread_bps"] > 0
    assert built[0]["option_type"] == "call"


def test_forward_return_is_computed():
    rows = []
    for ts, mid in [(0, 100), (60, 105), (180, 110)]:
        rows.append({
            "timestamp": ts,
            "observed_at": "2026-09-13T00:00:00Z",
            "underlying": "BTC",
            "instrument": "BTC-TEST-CALL",
            "option_type": "call",
            "strike": 77000,
            "expiry": None,
            "mid": mid,
        })
    out = attach_forward_returns(rows)
    assert out[0]["return_1m_pct"] == 5.0
