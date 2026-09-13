#!/usr/bin/env python3
"""Build research-ready option snapshots and forward-return tables.

This processor deliberately works from normalized captures and never invents
missing Greeks, IV, strike, expiry or underlying prices. It can therefore run
as CoinDCX's option-data schema evolves.
"""
from __future__ import annotations
import argparse, csv, json, math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HORIZONS_MINUTES = (1, 3, 5, 10, 15, 30)

def parse_ts(value: Any) -> float | None:
    if value is None: return None
    try:
        x = float(value)
        return x / 1000 if x > 10_000_000_000 else x
    except (TypeError, ValueError):
        return None

def num(value: Any) -> float | None:
    try: return float(value) if value is not None else None
    except (TypeError, ValueError): return None

def first_valid(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "")), None)

def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try: yield json.loads(line)
                except json.JSONDecodeError: continue

def load_normalized(path: Path) -> list[dict[str, Any]]:
    rows = []
    for row in read_jsonl(path):
        ts = parse_ts(first_valid(row.get("timestamp"), row.get("observed_at")))
        if ts is None: continue
        r = dict(row); r["_ts"] = ts
        rows.append(r)
    return rows

def contract_key(row: dict[str, Any]) -> str:
    if row.get("instrument") not in (None, ""): return str(row["instrument"])
    return "|".join(str(x or "") for x in (row.get("underlying"), row.get("expiry"), row.get("strike"), row.get("option_type")))

def build_snapshots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort observations and emit one row per contract observation with derived fields."""
    rows = sorted(rows, key=lambda r: (contract_key(r), r["_ts"]))
    out = []
    for r in rows:
        bid, ask, last = num(r.get("bid")), num(r.get("ask")), num(r.get("last"))
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask >= bid else first_valid(last, bid, ask)
        spread_bps = ((ask - bid) / mid * 10000) if bid is not None and ask is not None and mid not in (None, 0) and ask >= bid else None
        strike, index = num(r.get("strike")), num(r.get("index_price"))
        moneyness = math.log(index / strike) if index and strike and index > 0 and strike > 0 else None
        expiry_ts = parse_ts(r.get("expiry"))
        dte = (expiry_ts - r["_ts"]) / 86400 if expiry_ts is not None else None
        out.append({
            "timestamp": r["_ts"], "observed_at": r.get("observed_at"),
            "underlying": r.get("underlying"), "instrument": r.get("instrument"),
            "option_type": r.get("option_type"), "strike": strike, "expiry": r.get("expiry"),
            "bid": bid, "ask": ask, "mid": mid, "last": last, "mark_price": num(r.get("mark_price")),
            "index_price": index, "moneyness_log": moneyness, "dte_days": dte,
            "quantity": num(r.get("quantity")), "volume": num(r.get("volume")),
            "open_interest": num(r.get("open_interest")), "implied_volatility": num(r.get("implied_volatility")),
            "delta": num(r.get("delta")), "gamma": num(r.get("gamma")), "theta": num(r.get("theta")),
            "vega": num(r.get("vega")), "rho": num(r.get("rho")),
            "spread_bps": spread_bps, "source": r.get("source"),
        })
    return out

def attach_forward_returns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_contract: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows: by_contract[contract_key(r)].append(r)
    for series in by_contract.values():
        series.sort(key=lambda r: r["timestamp"])
        for i, row in enumerate(series):
            base = num(row.get("mid"))
            if base is None or base <= 0: continue
            for mins in HORIZONS_MINUTES:
                target = row["timestamp"] + mins * 60
                chosen = next((x for x in series[i + 1:] if x["timestamp"] >= target and num(x.get("mid")) is not None), None)
                row[f"return_{mins}m_pct"] = ((num(chosen.get("mid")) / base) - 1) * 100 if chosen else None
    return rows

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: path.write_text("", encoding="utf-8"); return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("input", type=Path); p.add_argument("--output", type=Path, default=Path("options_research.csv"))
    a = p.parse_args()
    rows = attach_forward_returns(build_snapshots(load_normalized(a.input)))
    write_csv(a.output, rows)
    print(json.dumps({"input_rows": len(rows), "output": str(a.output), "horizons_minutes": list(HORIZONS_MINUTES)}, indent=2))
    return 0
if __name__ == "__main__": raise SystemExit(main())
