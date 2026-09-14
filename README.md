# CoinDCX Options Market-Data Capture

This repository is dedicated to **CoinDCX crypto-options market-data capture and research**. It is intentionally separate from the Futures-only repository.

## Current scope

The collector records the public CoinDCX Options web application's market-data traffic using a real browser session. This avoids inventing undocumented Options API endpoints while still capturing the data the public options interface actually receives.

Supported underlyings currently targeted by the collector:

- BTC
- ETH
- SOL
- XAUT

CoinDCX currently advertises daily, weekly, monthly, quarterly and yearly option expiries. The collector does not assume a fixed strike list: it records whatever contracts the live option-chain page exposes.

## What is captured

Every run creates an immutable raw handoff containing:

- run metadata and collector version
- page URL and timestamps
- browser/network request metadata
- JSON/JSON-like HTTP responses whose URL or payload appears options-related
- WebSocket connection metadata and received frames when present
- embedded/framework page state and browser performance-resource diagnostics
- a normalized event stream where contract/quote/trade-like fields can be identified
- the raw page HTML snapshot at the end of the run

The raw network stream is retained because the exact upstream event schema can change. Normalization is additive and must never replace the raw source.

## Research-ready fields

Where available, normalization preserves or derives:

`timestamp`, `underlying`, `option_type`, `strike`, `expiry`, `instrument`, `symbol`, `bid`, `ask`, `last`, `mark_price`, `index_price`, `quantity`, `volume`, `open_interest`, `implied_volatility`, `delta`, `gamma`, `theta`, `vega`, `rho`, and source URL/event/channel metadata.

Not every field is guaranteed to be present from CoinDCX's public interface. Missing values are kept as null rather than inferred.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m src.options_collector --underlyings BTC ETH SOL XAUT --duration-minutes 5 --out-dir data/raw
```

## GitHub Actions and the CoinDCX block

GitHub-hosted runners are **not** used for the capture job. A real capture from the hosted runner received HTTP **403** from CoinDCX/Cloudflare and the saved page was the Cloudflare block page, so retrying different GitHub-hosted regions does not solve the underlying problem.

The Options Collector workflow therefore requires a self-hosted Linux x64 runner with these labels:

`self-hosted`, `linux`, `x64`, `options-capture`

The self-hosted machine must be on a network/IP from which `https://coindcx.com/options/btc` is reachable in a normal browser. The workflow performs a network preflight before starting the capture loop.

Setup details are documented in [`docs/SELF_HOSTED_OPTIONS_CAPTURE.md`](docs/SELF_HOSTED_OPTIONS_CAPTURE.md).

The workflow remains manually triggered and owns a bounded sequence of consecutive 5-minute segments. It archives raw capture and research outputs as GitHub Actions artifacts. It does not place orders and does not require exchange API credentials for public market-data collection.

## Safety / research boundary

This repository is a **market-data capture and research system only**. There is no automatic live-trading promotion path. No private account channels, order placement, or authenticated trading actions are implemented here.

## Why browser capture?

As of the current project audit, CoinDCX publicly documents extensive Futures API/WebSocket market-data interfaces, but a dedicated public Options API specification was not found in the official API reference. The public Options web product is live and exposes the options chain interface, so the collector observes the application's own public data transport rather than guessing undocumented endpoint contracts.

## Data quality rules

1. Preserve raw events before parsing.
2. Never discard an event merely because normalization fails.
3. Use UTC timestamps internally.
4. Keep one run isolated from another.
5. Record browser/network errors explicitly.
6. Never write API keys or secrets into artifacts.
7. Treat discovered schemas as empirical until CoinDCX publishes an official Options API contract.
