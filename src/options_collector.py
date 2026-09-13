#!/usr/bin/env python3
"""Capture public CoinDCX Options web market-data traffic."""
from __future__ import annotations
import argparse, hashlib, json, logging, re, signal, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from playwright.sync_api import Browser, Page, sync_playwright

LOG = logging.getLogger("coindcx-options")
KEYWORDS = ("option", "options", "expiry", "strike", "implied", "greek", "call", "put", "openinterest", "open_interest", "optionchain", "option-chain")
ALIASES = {
    "timestamp": ("timestamp", "ts", "T", "eventTimestamp", "event_time"), "instrument": ("instrument", "instrument_name", "symbol", "s", "pair", "contract"),
    "strike": ("strike", "strike_price", "strikePrice"), "expiry": ("expiry", "expiry_date", "expiryDate", "expiration", "expirationDate"),
    "option_type": ("option_type", "optionType", "right", "side", "type"), "bid": ("bid", "best_bid", "bestBid"), "ask": ("ask", "best_ask", "bestAsk"),
    "last": ("last", "last_price", "lastPrice", "ltp", "price", "p"), "mark_price": ("mark_price", "markPrice", "mark", "mp"),
    "index_price": ("index_price", "indexPrice", "index", "underlying_price"), "quantity": ("quantity", "qty", "q", "size", "amount"),
    "volume": ("volume", "vol", "24h_volume"), "open_interest": ("open_interest", "openInterest", "oi"), "implied_volatility": ("implied_volatility", "impliedVolatility", "iv", "IV"),
    "delta": ("delta", "Delta"), "gamma": ("gamma", "Gamma"), "theta": ("theta", "Theta"), "vega": ("vega", "Vega"), "rho": ("rho", "Rho"),
}

class StopFlag:
    def __init__(self) -> None: self.stop = False
    def request(self, *_: Any) -> None: LOG.info("Stop requested"); self.stop = True

def now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def is_optionish(value: str) -> bool: return any(k in value.lower() for k in KEYWORDS)
def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: return
    with path.open("a", encoding="utf-8") as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
def trim(text: str, limit: int) -> tuple[str, bool, str]:
    raw = text.encode("utf-8", errors="replace"); digest = hashlib.sha256(raw).hexdigest()
    if len(raw) <= limit: return text, False, digest
    return raw[:limit].decode("utf-8", errors="ignore") + f"\n...[truncated sha256={digest}]", True, digest)
def first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    low = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key in mapping: return mapping[key]
        if key.lower() in low: return low[key.lower()]
    return None

def normalize(payload: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []; stack = [payload]; visited = 0
    while stack and visited < 300_000:
        x = stack.pop(); visited += 1
        if isinstance(x, str):
            s = x.strip()
            if s.startswith("{") or s.startswith("["):
                try: stack.append(json.loads(s)); continue
                except Exception: pass
            continue
        if isinstance(x, list): stack.extend(x); continue
        if not isinstance(x, dict): continue
        item = {k: first(x, a) for k, a in ALIASES.items()}
        text = json.dumps(x, default=str, ensure_ascii=False)
        strong = sum(v is not None for v in item.values()) >= 3
        schema_hint = any(str(k).lower() in {"strike", "strikeprice", "expiry", "expirydate", "optiontype", "greeks", "iv", "impliedvolatility", "openinterest"} for k in x)
        if strong or schema_hint or is_optionish(text[:100000]):
            out.append({"observed_at": now(), "source": source, **item})
        stack.extend(v for v in x.values() if isinstance(v, (dict, list, str)))
    return out

def balanced_objects(text: str, limit: int = 5000) -> list[str]:
    """Extract balanced {...} JSON candidates without assuming flat objects."""
    found: list[str] = []; starts: list[int] = []; in_str = False; esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True; continue
        if ch == '{': starts.append(i)
        elif ch == '}' and starts:
            start = starts.pop()
            if len(found) < limit: found.append(text[start:i+1])
    return found

def normalize_text_payload(text: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try: rows.extend(normalize(json.loads(text), source))
    except Exception: pass
    for candidate in balanced_objects(text):
        if not re.search(r'(?i)(strike|expiry|option.?type|implied.?volatility|open.?interest|greeks)', candidate): continue
        try: rows.extend(normalize(json.loads(candidate), source))
        except Exception: continue
    seen = set(); out = []
    for row in rows:
        key = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
        if key not in seen: seen.add(key); out.append(row)
    return out

def err(stage: str, underlying: str, exc: Exception, **extra: Any) -> dict[str, Any]:
    return {"observed_at": now(), "kind": "collector_error", "stage": stage, "underlying": underlying, "error": f"{type(exc).__name__}: {exc}"[:2000], **extra}

def capture_page_state(page: Page, underlying: str, run_dir: Path, stats: dict[str, int]) -> None:
    """Persist framework-embedded JSON state and performance resource diagnostics."""
    norm = run_dir / "normalized_events.jsonl"; net = run_dir / "network_events.jsonl"
    try:
        scripts = page.locator("script").all()
        for script in scripts:
            try:
                typ = (script.get_attribute("type") or "").lower()
                txt = script.text_content(timeout=1000) or ""
                if not txt or (typ not in {"application/json", "application/ld+json"} and not is_optionish(txt[:200000])): continue
                clipped, truncated, digest = trim(txt, 2_000_000)
                write_rows(net, [{"observed_at": now(), "kind": "embedded_script", "underlying": underlying, "script_type": typ, "body_truncated": truncated, "body_sha256": digest, "body": clipped}])
                rows = normalize_text_payload(txt, {"transport": "embedded_script", "underlying": underlying, "script_type": typ})
                write_rows(norm, rows); stats["normalized"] += len(rows)
            except Exception: continue
    except Exception as exc:
        stats["errors"] += 1; write_rows(net, [err("embedded_script", underlying, exc)])
    try:
        resources = page.evaluate("""() => performance.getEntriesByType('resource').map(e => ({name:e.name, initiatorType:e.initiatorType, duration:e.duration, transferSize:e.transferSize})).slice(-500)""")
        write_rows(net, [{"observed_at": now(), "kind": "performance_resources", "underlying": underlying, "resources": resources}])
    except Exception as exc:
        stats["errors"] += 1; write_rows(net, [err("performance_resources", underlying, exc)])

def attach(page: Page, underlying: str, run_dir: Path, limit: int, stats: dict[str, int], stop: StopFlag) -> None:
    net, ws_path, norm = run_dir / "network_events.jsonl", run_dir / "websocket_frames.jsonl", run_dir / "normalized_events.jsonl"
    def request(req: Any) -> None:
        if stop.stop: return
        try:
            rt = req.resource_type
            if rt not in {"xhr", "fetch", "document", "eventsource", "websocket", "other"}: return
            write_rows(net, [{"observed_at": now(), "kind": "http_request", "underlying": underlying, "resource_type": rt, "method": req.method, "url": req.url}])
        except Exception as exc: stats["errors"] += 1; write_rows(net, [err("request", underlying, exc)])
    def response(r: Any) -> None:
        if stop.stop: return
        try:
            req = r.request; rt = getattr(req, "resource_type", ""); ct, url = r.headers.get("content-type", ""), r.url
            if rt not in {"xhr", "fetch", "document", "eventsource", "other"} and not is_optionish(url): return
            body, truncated, digest = trim(r.text(), limit)
            write_rows(net, [{"observed_at": now(), "kind": "http_response", "underlying": underlying, "url": url, "resource_type": rt, "status": r.status, "content_type": ct, "body_truncated": truncated, "body_sha256": digest, "body": body}])
            stats["http_json"] += int("json" in ct.lower())
            if not truncated:
                rows = normalize_text_payload(body, {"transport": "http", "resource_type": rt, "url": url, "content_type": ct, "underlying": underlying}); write_rows(norm, rows); stats["normalized"] += len(rows)
        except Exception as exc: stats["errors"] += 1; write_rows(net, [err("response", underlying, exc)])
    def websocket(sock: Any) -> None:
        source = {"transport": "websocket", "url": sock.url, "underlying": underlying}; write_rows(net, [{"observed_at": now(), "kind": "websocket_opened", **source}])
        def frame(data: Any) -> None:
            if stop.stop: return
            try:
                text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data); text, truncated, digest = trim(text, limit)
                write_rows(ws_path, [{"observed_at": now(), "kind": "websocket_frame_received", **source, "body_truncated": truncated, "body_sha256": digest, "body": text}]); stats["websocket_frames"] += 1
                if not truncated:
                    rows = normalize_text_payload(text, source); write_rows(norm, rows); stats["normalized"] += len(rows)
            except Exception as exc: stats["errors"] += 1; write_rows(ws_path, [err("websocket_frame", underlying, exc)])
        sock.on("framereceived", frame)
    page.on("request", request); page.on("response", response); page.on("websocket", websocket)

def sweep_expiries(page: Page, underlying: str, stats: dict[str, int]) -> None:
    try:
        controls = page.locator("button, [role='button'], a").all(); targets = []
        for control in controls:
            try:
                text = re.sub(r"\s+", " ", control.inner_text(timeout=500)).strip()
                if re.search(r"\b(?:\d{1,2}\s+[A-Za-z]{3}|\d{1,2}\s+[A-Za-z]+)\b", text): targets.append((control, text))
            except Exception: continue
        seen = set()
        for control, text in targets:
            if text in seen or not control.is_visible(): continue
            seen.add(text)
            try: control.click(timeout=1000); page.wait_for_timeout(750); stats["expiry_tabs_clicked"] = stats.get("expiry_tabs_clicked", 0) + 1
            except Exception: continue
    except Exception: stats["expiry_sweep_errors"] = stats.get("expiry_sweep_errors", 0) + 1

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--duration-minutes", type=int, default=120); p.add_argument("--underlyings", nargs="+", default=["BTC", "ETH", "SOL", "XAUT"]); p.add_argument("--out-dir", default="data/raw"); p.add_argument("--headed", action="store_true")
    a = p.parse_args()
    if not 1 <= a.duration_minutes <= 230: raise SystemExit("duration-minutes must be between 1 and 230")
    stop = StopFlag(); signal.signal(signal.SIGINT, stop.request); signal.signal(signal.SIGTERM, stop.request)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_options"; run_dir = Path(a.out_dir) / run_id; run_dir.mkdir(parents=True, exist_ok=True); started = now(); names = [x.upper() for x in a.underlyings]
    (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": run_id, "started_at": started, "requested_duration_minutes": a.duration_minutes, "underlyings": names, "collector_version": "options-browser-capture-v5", "source_of_truth": "raw browser network events", "secrets_written": False}, indent=2), encoding="utf-8")
    stats = {"http_json": 0, "websocket_frames": 0, "normalized": 0, "errors": 0, "expiry_tabs_clicked": 0, "expiry_sweep_errors": 0}
    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=not a.headed); context = browser.new_context(locale="en-IN", timezone_id="UTC", viewport={"width": 1440, "height": 1000}); pages: list[tuple[Page, str]] = []
        for name in names:
            page = context.new_page(); attach(page, name, run_dir, 2_000_000, stats); url = f"https://coindcx.com/options/{name.lower()}"
            try: page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            except Exception as exc: stats["errors"] += 1; write_rows(run_dir / "network_events.jsonl", [err("goto", name, exc, url=url)])
            page.wait_for_timeout(8_000); capture_page_state(page, name, run_dir, stats); pages.append((page, name))
        end = time.monotonic() + a.duration_minutes * 60; next_sweep = 0.0
        while not stop.stop and time.monotonic() < end:
            for page, _name in pages: page.wait_for_timeout(1_000)
            if time.monotonic() >= next_sweep:
                for page, name in pages:
                    sweep_expiries(page, name, stats); capture_page_state(page, name, run_dir, stats)
                next_sweep = time.monotonic() + 30
        for page, name in pages:
            try: (run_dir / f"page_{name.lower()}.html").write_text(page.content(), encoding="utf-8")
            except Exception as exc: stats["errors"] += 1; write_rows(run_dir / "network_events.jsonl", [err("html", name, exc)])
            page.close()
        context.close(); browser.close()
    (run_dir / "run_summary.json").write_text(json.dumps({"run_id": run_id, "started_at": started, "finished_at": now(), "stats": stats, "stopped_by_signal": stop.stop}, indent=2), encoding="utf-8")
    LOG.info("Completed: %s", stats); return 0
if __name__ == "__main__": logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"); raise SystemExit(main())
