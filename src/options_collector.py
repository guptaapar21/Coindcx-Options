#!/usr/bin/env python3
"""Capture public CoinDCX Options web market-data traffic."""
from __future__ import annotations
import argparse, hashlib, json, logging, signal, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from playwright.sync_api import Browser, Page, sync_playwright

LOG = logging.getLogger("coindcx-options")
KEYWORDS = ("option", "options", "expiry", "strike", "implied", "greek", "call", "put", "openinterest", "open_interest", "optionchain", "option-chain")
ALIASES = {
    "timestamp": ("timestamp", "ts", "T", "eventTimestamp", "event_time"),
    "instrument": ("instrument", "instrument_name", "symbol", "s", "pair", "contract"),
    "strike": ("strike", "strike_price", "strikePrice"),
    "expiry": ("expiry", "expiry_date", "expiryDate", "expiration", "expirationDate"),
    "option_type": ("option_type", "optionType", "right"),
    "bid": ("bid", "best_bid", "bestBid"), "ask": ("ask", "best_ask", "bestAsk"),
    "last": ("last", "last_price", "lastPrice", "ltp", "price", "p"),
    "mark_price": ("mark_price", "markPrice", "mark", "mp"),
    "index_price": ("index_price", "indexPrice", "index", "underlying_price"),
    "quantity": ("quantity", "qty", "q", "size", "amount"), "volume": ("volume", "vol", "24h_volume"),
    "open_interest": ("open_interest", "openInterest", "oi"),
    "implied_volatility": ("implied_volatility", "impliedVolatility", "iv", "IV"),
    "delta": ("delta", "Delta"), "gamma": ("gamma", "Gamma"), "theta": ("theta", "Theta"), "vega": ("vega", "Vega"), "rho": ("rho", "Rho"),
}

class StopFlag:
    def __init__(self) -> None: self.stop = False
    def request(self, *_: Any) -> None:
        LOG.info("Stop requested"); self.stop = True

def now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def is_optionish(value: str) -> bool: return any(k in value.lower() for k in KEYWORDS)
def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: return
    with path.open("a", encoding="utf-8") as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
def trim(text: str, limit: int) -> tuple[str, bool, str]:
    raw = text.encode("utf-8", errors="replace"); digest = hashlib.sha256(raw).hexdigest()
    if len(raw) <= limit: return text, False, digest
    return raw[:limit].decode("utf-8", errors="ignore") + f"\n...[truncated sha256={digest}]", True, digest
def first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    low = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key in mapping: return mapping[key]
        if key.lower() in low: return low[key.lower()]
    return None
def normalize(payload: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []; stack = [payload]
    while stack:
        x = stack.pop()
        if isinstance(x, list): stack.extend(x); continue
        if not isinstance(x, dict): continue
        item = {k: first(x, a) for k, a in ALIASES.items()}
        text = json.dumps(x, default=str, ensure_ascii=False)
        if sum(v is not None for v in item.values()) >= 3 or is_optionish(text[:20000]) or any(k in x for k in ("strike", "strikePrice", "expiry", "expiryDate", "optionType", "greeks")):
            out.append({"observed_at": now(), "source": source, **item})
        stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
    return out
def err(stage: str, underlying: str, exc: Exception, **extra: Any) -> dict[str, Any]:
    return {"observed_at": now(), "kind": "collector_error", "stage": stage, "underlying": underlying, "error": f"{type(exc).__name__}: {exc}"[:2000], **extra}

def attach(page: Page, underlying: str, run_dir: Path, limit: int, stats: dict[str, int], stop: StopFlag) -> None:
    net, ws_path, norm = run_dir / "network_events.jsonl", run_dir / "websocket_frames.jsonl", run_dir / "normalized_events.jsonl"
    def response(r: Any) -> None:
        if stop.stop: return
        try:
            ct, url = r.headers.get("content-type", ""), r.url
            if "json" not in ct.lower() and not is_optionish(url): return
            body, truncated, digest = trim(r.text(), limit)
            write_rows(net, [{"observed_at": now(), "kind": "http_response", "underlying": underlying, "url": url, "status": r.status, "content_type": ct, "body_truncated": truncated, "body_sha256": digest, "body": body}])
            stats["http_json"] += 1
            if not truncated:
                rows = normalize(json.loads(body) if body[:1] in "[{" else body, {"transport": "http", "url": url, "underlying": underlying})
                write_rows(norm, rows); stats["normalized"] += len(rows)
        except Exception as exc:
            stats["errors"] += 1; write_rows(net, [err("response", underlying, exc)])
    def websocket(sock: Any) -> None:
        source = {"transport": "websocket", "url": sock.url, "underlying": underlying}
        def frame(data: Any) -> None:
            if stop.stop: return
            try:
                text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
                text, truncated, digest = trim(text, limit)
                write_rows(ws_path, [{"observed_at": now(), "kind": "websocket_frame_received", **source, "body_truncated": truncated, "body_sha256": digest, "body": text}])
                stats["websocket_frames"] += 1
                if not truncated:
                    try: payload = json.loads(text)
                    except Exception: payload = text
                    rows = normalize(payload, source); write_rows(norm, rows); stats["normalized"] += len(rows)
            except Exception as exc:
                stats["errors"] += 1; write_rows(ws_path, [err("websocket_frame", underlying, exc)])
        sock.on("framereceived", frame)
    page.on("response", response); page.on("websocket", websocket)

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--duration-minutes", type=int, default=120); p.add_argument("--underlyings", nargs="+", default=["BTC", "ETH", "SOL", "XAUT"]); p.add_argument("--out-dir", default="data/raw"); p.add_argument("--headed", action="store_true")
    a = p.parse_args()
    if not 1 <= a.duration_minutes <= 230: raise SystemExit("duration-minutes must be between 1 and 230")
    stop = StopFlag(); signal.signal(signal.SIGINT, stop.request); signal.signal(signal.SIGTERM, stop.request)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_options"; run_dir = Path(a.out_dir) / run_id; run_dir.mkdir(parents=True, exist_ok=True)
    started = now(); names = [x.upper() for x in a.underlyings]
    (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": run_id, "started_at": started, "requested_duration_minutes": a.duration_minutes, "underlyings": names, "collector_version": "options-browser-capture-v2", "source_of_truth": "raw browser network events", "secrets_written": False}, indent=2), encoding="utf-8")
    stats = {"http_json": 0, "websocket_frames": 0, "normalized": 0, "errors": 0}
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=not a.headed); context = browser.new_context(locale="en-IN", timezone_id="UTC", viewport={"width": 1440, "height": 1000}); pages: list[tuple[Page, str]] = []
        for name in names:
            page = context.new_page(); attach(page, name, run_dir, 2_000_000, stats, stop)
            url = f"https://coindcx.com/options/{name.lower()}"
            try: page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            except Exception as exc: stats["errors"] += 1; write_rows(run_dir / "network_events.jsonl", [err("goto", name, exc, url=url)])
            pages.append((page, name))
        end = time.monotonic() + a.duration_minutes * 60
        while not stop.stop and time.monotonic() < end:
            for page, _ in pages:
                page.wait_for_timeout(1_000)
        for page, name in pages:
            try: (run_dir / f"page_{name.lower()}.html").write_text(page.content(), encoding="utf-8")
            except Exception as exc: stats["errors"] += 1; write_rows(run_dir / "network_events.jsonl", [err("html", name, exc)])
            page.close()
        context.close(); browser.close()
    (run_dir / "run_summary.json").write_text(json.dumps({"run_id": run_id, "started_at": started, "finished_at": now(), "stats": stats, "stopped_by_signal": stop.stop}, indent=2), encoding="utf-8")
    LOG.info("Completed: %s", stats); return 0
if __name__ == "__main__": logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"); raise SystemExit(main())
