#!/usr/bin/env python3
"""Capture public CoinDCX Options web market-data traffic.

The public CoinDCX Options UI currently exposes the option-chain experience, but
its dedicated Options API/WebSocket contract is not published in the official
API reference. This collector therefore records the live browser transport
without hard-coding an undocumented endpoint schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Page, sync_playwright

LOGGER = logging.getLogger("coindcx-options")

OPTION_KEYWORDS = (
    "option", "options", "expiry", "strike", "implied", "iv", "greek",
    "call", "put", "openinterest", "open_interest", "optionchain", "option-chain",
)

FIELD_ALIASES = {
    "timestamp": ("timestamp", "ts", "T", "eventTimestamp", "event_time"),
    "instrument": ("instrument", "instrument_name", "symbol", "s", "pair", "contract"),
    "strike": ("strike", "strike_price", "strikePrice"),
    "expiry": ("expiry", "expiry_date", "expiryDate", "expiration", "expirationDate"),
    "option_type": ("option_type", "optionType", "type", "side", "right"),
    "bid": ("bid", "best_bid", "bestBid"),
    "ask": ("ask", "best_ask", "bestAsk"),
    "last": ("last", "last_price", "lastPrice", "ltp", "price", "p"),
    "mark_price": ("mark_price", "markPrice", "mark", "mp"),
    "index_price": ("index_price", "indexPrice", "index", "underlying_price"),
    "quantity": ("quantity", "qty", "q", "size", "amount"),
    "volume": ("volume", "vol", "24h_volume"),
    "open_interest": ("open_interest", "openInterest", "oi"),
    "implied_volatility": ("implied_volatility", "impliedVolatility", "iv", "IV"),
    "delta": ("delta", "Delta"),
    "gamma": ("gamma", "Gamma"),
    "theta": ("theta", "Theta"),
    "vega": ("vega", "Vega"),
    "rho": ("rho", "Rho"),
}


@dataclass(frozen=True)
class CollectorConfig:
    duration_minutes: int = 120
    page_timeout_ms: int = 120_000
    idle_wait_ms: int = 1_500
    underlyings: tuple[str, ...] = ("BTC", "ETH", "SOL", "XAUT")
    url_template: str = "https://coindcx.com/options/{underlying_lower}"
    headless: bool = True
    max_event_bytes: int = 2_000_000
    save_html: bool = True
    save_screenshot: bool = False


class StopFlag:
    def __init__(self) -> None:
        self.stop = False

    def request(self, *_: Any) -> None:
        LOGGER.info("Stop requested")
        self.stop = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def contains_option_keyword(value: str) -> bool:
    text = value.lower()
    return any(k in text for k in OPTION_KEYWORDS)


def truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value, False
    digest = hashlib.sha256(encoded).hexdigest()
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + f"\n...[truncated sha256={digest}]", True


def jsonish(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def first_present(mapping: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in aliases:
        if key in mapping:
            return mapping[key]
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def normalize_dicts(payload: Any, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk JSON-like payloads and emit flat records where option fields exist."""
    records: list[dict[str, Any]] = []
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            normalized = {field: first_present(item, aliases) for field, aliases in FIELD_ALIASES.items()}
            present = sum(value is not None for value in normalized.values())
            optionish = (
                present >= 3
                or any(k in item for k in ("strike", "strikePrice", "expiry", "expiryDate", "option_type", "optionType", "greeks"))
                or contains_option_keyword(json.dumps(item, default=str)[:20_000])
            )
            if optionish:
                record = {
                    "observed_at": utc_now(),
                    "source": source,
                    **normalized,
                }
                records.append(record)
            for value in item.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return records


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def safe_error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:2_000]


def collect_underlying(page: Page, underlying: str, cfg: CollectorConfig, run_dir: Path, stop: StopFlag) -> dict[str, int]:
    stats = {"http_json": 0, "websocket_frames": 0, "normalized": 0, "errors": 0}
    events_path = run_dir / "network_events.jsonl"
    websocket_path = run_dir / "websocket_frames.jsonl"
    normalized_path = run_dir / "normalized_events.jsonl"

    def on_response(response: Any) -> None:
        if stop.stop:
            return
        try:
            headers = response.headers
            content_type = headers.get("content-type", "")
            url = response.url
            relevant = "json" in content_type.lower() or contains_option_keyword(url)
            if not relevant:
                return
            text = response.text()
            text, truncated = truncate_text(text, cfg.max_event_bytes)
            row = {
                "observed_at": utc_now(),
                "kind": "http_response",
                "underlying": underlying,
                "url": url,
                "status": response.status,
                "content_type": content_type,
                "body_truncated": truncated,
                "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "body": text,
            }
            write_jsonl(events_path, [row])
            stats["http_json"] += 1
            if not truncated:
                payload = jsonish(text)
                normalized = normalize_dicts(payload, {"transport": "http", "url": url, "underlying": underlying})
                write_jsonl(normalized_path, normalized)
                stats["normalized"] += len(normalized)
        except Exception as exc:
            stats["errors"] += 1
            write_jsonl(events_path, [{"observed_at": utc_now(), "kind": "collector_error", "stage": "response", "underlying": underlying, "error": safe_error_text(exc)}])

    def on_websocket(ws: Any) -> None:
        source = {"transport": "websocket", "url": ws.url, "underlying": underlying}

        def on_frame(frame: Any) -> None:
            if stop.stop:
                return
            try:
                if isinstance(frame, bytes):
                    text = frame.decode("utf-8", errors="replace")
                else:
                    text = str(frame)
                text, truncated = truncate_text(text, cfg.max_event_bytes)
                write_jsonl(websocket_path, [{
                    "observed_at": utc_now(),
                    "kind": "websocket_frame_received",
                    **source,
                    "body_truncated": truncated,
                    "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "body": text,
                }])
                stats["websocket_frames"] += 1
                if not truncated:
                    normalized = normalize_dicts(jsonish(text), source)
                    write_jsonl(normalized_path, normalized)
                    stats["normalized"] += len(normalized)
            except Exception as exc:
                stats["errors"] += 1
                write_jsonl(websocket_path, [{"observed_at": utc_now(), "kind": "collector_error", "stage": "websocket_frame", "underlying": underlying, "error": safe_error_text(exc)}])

        ws.on("framereceived", on_frame)

    page.on("response", on_response)
    page.on("websocket", on_websocket)

    url = cfg.url_template.format(underlying_lower=underlying.lower(), underlying=underlying)
    LOGGER.info("Opening %s", url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=cfg.page_timeout_ms)
    except Exception as exc:
        stats["errors"] += 1
        write_jsonl(events_path, [{"observed_at": utc_now(), "kind": "collector_error", "stage": "goto", "underlying": underlying, "url": url, "error": safe_error_text(exc)}])

    # Let the option chain populate and keep the page alive for the requested run duration.
    end_at = time.monotonic() + cfg.duration_minutes * 60
    while not stop.stop and time.monotonic() < end_at:
        try:
            page.wait_for_timeout(max(250, cfg.idle_wait_ms))
        except Exception as exc:
            stats["errors"] += 1
            break

    if cfg.save_html:
        try:
            html = page.content()
            (run_dir / f"page_{underlying.lower()}.html").write_text(html, encoding="utf-8")
        except Exception as exc:
            stats["errors"] += 1
            write_jsonl(events_path, [{"observed_at": utc_now(), "kind": "collector_error", "stage": "html", "underlying": underlying, "error": safe_error_text(exc)}])
    if cfg.save_screenshot:
        try:
            page.screenshot(path=str(run_dir / f"page_{underlying.lower()}.png"), full_page=True)
        except Exception as exc:
            stats["errors"] += 1
            write_jsonl(events_path, [{"observed_at": utc_now(), "kind": "collector_error", "stage": "screenshot", "underlying": underlying, "error": safe_error_text(exc)}])

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-minutes", type=int, default=120)
    parser.add_argument("--underlyings", nargs="+", default=["BTC", "ETH", "SOL", "XAUT"])
    parser.add_argument("--out-dir", default="data/raw")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--save-screenshot", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration_minutes < 1 or args.duration_minutes > 230:
        raise SystemExit("duration-minutes must be between 1 and 230")

    stop = StopFlag()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    started = utc_now()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_options"
    run_dir = Path(args.out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = CollectorConfig(
        duration_minutes=args.duration_minutes,
        underlyings=tuple(args.underlyings),
        headless=not args.headed,
        save_screenshot=args.save_screenshot,
    )
    (run_dir / "run_metadata.json").write_text(json.dumps({
        "run_id": run_id,
        "started_at": started,
        "requested_duration_minutes": cfg.duration_minutes,
        "underlyings": list(cfg.underlyings),
        "url_template": cfg.url_template,
        "collector_version": "options-browser-capture-v1",
        "source_of_truth": "raw browser network events",
        "secrets_written": False,
    }, indent=2), encoding="utf-8")

    total_stats: dict[str, int] = {"http_json": 0, "websocket_frames": 0, "normalized": 0, "errors": 0}
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=cfg.headless)
        context = browser.new_context(
            locale="en-IN",
            timezone_id="UTC",
            viewport={"width": 1440, "height": 1000},
            ignore_https_errors=False,
        )
        for underlying in cfg.underlyings:
            if stop.stop:
                break
            page = context.new_page()
            stats = collect_underlying(page, underlying.upper(), cfg, run_dir, stop)
            for key, value in stats.items():
                total_stats[key] += value
            page.close()
        browser.close()

    (run_dir / "run_summary.json").write_text(json.dumps({
        "run_id": run_id,
        "started_at": started,
        "finished_at": utc_now(),
        "stats": total_stats,
        "stopped_by_signal": stop.stop,
    }, indent=2), encoding="utf-8")
    LOGGER.info("Completed: %s", total_stats)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
