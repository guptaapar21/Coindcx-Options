#!/usr/bin/env python3
"""Fail loudly when an options capture artifact is structurally unusable."""
from __future__ import annotations
import json, sys
from pathlib import Path

REQUIRED = ("run_metadata.json", "run_summary.json", "network_events.jsonl", "websocket_frames.jsonl", "normalized_events.jsonl")

def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data/raw")
    runs = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not runs:
        raise SystemExit("No options run directory found")
    run = runs[-1]
    missing = [name for name in REQUIRED if not (run / name).exists()]
    if missing:
        raise SystemExit(f"Missing required capture files: {missing}")
    metadata = json.loads((run / "run_metadata.json").read_text())
    summary = json.loads((run / "run_summary.json").read_text())
    if metadata.get("secrets_written") is not False:
        raise SystemExit("Capture metadata does not certify secrets_written=false")
    stats = summary.get("stats", {})
    print(json.dumps({"run": run.name, "underlyings": metadata.get("underlyings"), "stats": stats}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
