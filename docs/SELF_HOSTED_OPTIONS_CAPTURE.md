# Self-hosted CoinDCX Options capture

## Why this is required

GitHub-hosted runners are not suitable for the browser capture because CoinDCX blocks their outbound IP ranges. The repository's capture workflow therefore requires a self-hosted Linux x64 runner with the label `options-capture`.

## Runner setup

Install a current GitHub Actions runner on a machine/network that can open `https://coindcx.com/options/btc` in a normal browser.

Register the runner with these labels:

- `self-hosted`
- `linux`
- `x64`
- `options-capture`

The machine needs Python 3.11+, Git, and permission to install Chromium/Playwright dependencies. The workflow installs the Python packages and Playwright browser itself.

Before using Actions, verify from that machine:

```bash
curl -I -A 'Mozilla/5.0' https://coindcx.com/options/btc
```

A normal successful HTTP response is expected. If the network is challenged or blocked, use a different residential/network egress rather than adding bypass logic to the collector.

## Start capture

Run **Options Collector** from GitHub Actions with the required runner online. The default is 12 consecutive 5-minute segments for BTC, ETH, SOL and XAUT.

The workflow will:

1. open CoinDCX Options pages from the self-hosted runner;
2. preserve raw HTTP/browser traffic and page state;
3. normalize actual option records;
4. validate that normalized records exist;
5. generate the research table; and
6. upload the raw capture and research artifacts to the workflow run.

## Important

Do not weaken validation just to turn a blocked or empty capture green. A successful run must contain real normalized option events. Raw browser data remains the source of truth.
