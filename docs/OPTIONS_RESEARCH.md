# Options research design

## Objective

Use CoinDCX Options data as the expression layer for the underlying-flow hypotheses being discovered in the separate Futures research project.

The Options repository does **not** copy the Futures collector or the Futures research state. It records the option surface independently and provides fields needed to later join an observed Futures signal to an actual option response.

## Required research dimensions

For every observed option contract, retain the timestamped quote/trade state where available:

- underlying (BTC, ETH, SOL, XAUT)
- call/put
- strike
- expiry
- bid/ask/last/mid/mark
- quantity and volume
- open interest
- implied volatility
- Greeks: delta, gamma, theta, vega, rho
- underlying/index price
- data-source transport and raw-event linkage

## Derived measurements

The processor derives:

- bid/ask spread in basis points
- log-moneyness when underlying and strike are known
- days to expiry when expiry is timestamp-like
- option mid-price forward returns at 1m, 3m, 5m, 10m, 15m and 30m

No missing Greek, IV, expiry, strike or price is imputed.

## Futures-signal linkage

The intended next research stage is to import an immutable Futures-signal event stream with at least:

`timestamp, underlying, signal_id, direction, hypothesis_name`

For each signal, evaluate a controlled menu of option expressions:

- ATM call/put
- near-ATM call/put
- slightly OTM call/put
- expiry buckets
- outright option versus defined-risk spread

The study must compare option P&L against the same signal expressed in Futures, including transaction costs and realistic bid/ask execution.

## Important option-specific issues

A directional signal is not sufficient for an options strategy. The study must separately measure:

- IV expansion/contraction
- theta decay
- gamma exposure
- bid/ask spread
- time-to-expiry
- strike/moneyness
- liquidity and fillability

CoinDCX currently advertises BTC, ETH, SOL and XAUT options, with daily/weekly/monthly/quarterly/yearly expiries. Its help centre states options are cash-settled in INR at expiry and uses a dedicated Options Wallet. These are product facts, not assumptions about an API schema.

## Validation rule

A candidate option expression is not promoted merely because the option premium rose. It must demonstrate robustness across time and, where sample size permits, across underlying assets and expiries, while surviving execution-cost and IV/theta sensitivity checks.
