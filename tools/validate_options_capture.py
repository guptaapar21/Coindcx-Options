#!/usr/bin/env python3
"""Fail loudly when an options capture artifact is structurally unusable."""
from __future__ import annotations
import json, sys
from pathlib import Path

REQUIRED = ("run_metadata.json", "run_summary.json", "network_events.jsonl", "normalized_events.jsonl")

def nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0

def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not runs:
        raise SystemExit("No options run directory found")
    run = runs[-1]
    missing = [name for name in REQUIRED if not nonempty(run / name)]
    if missing:
        raise SystemExit(f"Missing required capture files: {missing}")
    metadata = json.loads((run / "run_metadata.json").read_text())
    summary = json.loads((run / "run_summary.json").read_text())
    if metadata.get("secrets_written") is not False:
        raise SystemExit("Capture metadata does not certify secrets_written=false")
    stats = summary.get("stats", {})
    # Native WebSocket traffic is optional: some public web sessions use XHR,
    # fetch, SSE, long-polling or framework data transports instead.
    websocket_file = run / "websocket_frames.jsonl"
    if not nonempty(websocket_file) and not stats.get("http_json", 0) and not any(k in summary.get("stats", {}) for k in ("normalized",)):
        raise SystemExit("No usable market-data transport captured")
    if int(stats.get("normalized", 0) or 0) <= 0:
        raise SystemExit("No normalized option events captured")
    print(json.dumps({"run": run.name, "underlyings": metadata.get("underlyings"), "stats": stats, "websocket_capture": nonempty(websocket_file)}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
