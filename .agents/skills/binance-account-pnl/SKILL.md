---
name: binance-account-pnl
description: "Read-only status check of a Binance USD-M futures account (testnet, demo or live): balances, margin, positions, open orders, and realised / unrealised / funding P&L as % of combined capital (all stablecoin deposits together), with a single historical chart. Use when asked for the account's status, P&L percentages, or a P&L graph. GET requests only; never trades."
---

# binance-account-pnl

Produces, from GET requests only:
- `summary.md` / `summary.json`: balances, margin, positions (long/short/net/gross), open orders, and P&L in USD and % of capital.
- `pnl_history.png`: ONE chart with four cumulative lines, all as % of capital: **Total**, **Unrealised**, **Realised (closed trades − fees)**, **Funding** (dashed).

## Rules
1. **Read only.** `scripts/bn_readonly.py` has a GET-only client with an endpoint allow-list. Never add a write endpoint, and never call `/fapi/v1/order`, `/leverage`, `/marginType`, `/positionSide`, transfers or withdrawals. If the user asks for any action on the account, refuse and say this skill is read-only.
2. **Secrets.** Pass keys inline by name on the single command that needs them, wrapping each secret name in the secret-placeholder character (U+26BF), written below as `SECRET{NAME}`:
   `BN_KEY='SECRET{API_KEY_NAME}' BN_SECRET='SECRET{SECRET_KEY_NAME}' python3 ...`
   Never export them, write them to a file, or print them. Only `fetch_account.py` needs keys; `report.py` runs offline. Run `list_secrets` to find the names. On this desk they are `BINANCE_DEMO_API_KEY` / `BINANCE_DEMO_SECRET_KEY`, which belong to the **futures testnet**.
3. **Which venue.** `--env testnet|demo|live` or a base URL. From this sandbox, `demo-fapi`, `demo-api`, spot testnet and live Binance return **HTTP 451 (restricted location)**; `testnet.binancefuture.com` works. When a venue returns 451, say which one, and don't try VPN or proxy workarounds.
4. **Capital base is combined.** The base is the sum of all stablecoin deposits at 1:1 (USDT + USDC + FDUSD …), taken from `TRANSFER` income rows. A balance with no transfer row is backed out as wallet balance minus income booked since. Non-stable assets (BTC, BNB) are left out of the base and listed as excluded; pass `--include-nonstable` to value them at the latest `<ASSET>USDT` close. Always state the base in the reply (e.g. "5,000 USDT + 5,000 USDC = 10,000").

## Commands
```bash
S=<skill dir>/scripts; OUT=/home/user/scratch_bn/run_$(date -u +%Y%m%dT%H%M)
mkdir -p $OUT
# 1. fetch (1-3 min for ~150 symbols -> use execute_shell_background, then poll)
BN_KEY='SECRET{BINANCE_DEMO_API_KEY}' BN_SECRET='SECRET{BINANCE_DEMO_SECRET_KEY}' \
  python3 $S/fetch_account.py --env testnet --out $OUT/data.json [--days 90]
# 2. report (offline, seconds)
python3 $S/report.py $OUT/data.json --outdir $OUT
```
Then `publish_file $OUT/pnl_history.png` and embed the snippet the tool returns. Needs `matplotlib` (`pip install matplotlib` if missing).

## How the numbers are built
- **Realised P&L, commission, funding:** exact values from `/fapi/v1/income`, summed by type in USD (stablecoins at 1:1).
- **Unrealised history:** Binance doesn't store it, so it is rebuilt. Every fill from `/fapi/v1/userTrades` is replayed against 15-minute closes from `/fapi/v1/klines`. On a 15-minute grid: unrealised = Σ qty·close − Σ qty·fill price − closed-trade P&L.
- **The last point uses the exchange's own figure.** It is set to `totalUnrealizedProfit` (mark price), so the headline matches the exchange. The rebuilt last value and the exchange value are both in `summary.json` under `unrealised_rebuilt_last_vs_exchange`. A gap of a few tens of USD is normal (15-minute close vs live mark price). A large gap means fills or klines are missing, so say so.
- **Total** = realised (trades − fees + other) + funding + unrealised.
- **Percentages** are % of the combined capital base. Add per-position % of entry notional or % of gross only if asked, and label them clearly.

## Reply format
1. The venue and the as-of time (UTC), plus the history start, so the user knows how short the window is.
2. The capital base, spelled out.
3. A table with closed-trade P&L, commission, realised net, funding, unrealised and total, in both USD and %.
4. One line on positions: count, long/short, net, gross (and as a multiple of margin balance), open orders, largest and best/worst names.
5. The chart.
6. Caveats, only if they apply: testnet prices, a short history, a big gap between the rebuilt and exchange unrealised value, excluded assets, any venue that returned 451.

## Pitfalls
- `/fapi/v1/income` returns at most 1000 rows per call and a 7-day window; the script pages through both.
- `userTrades` is limited to 7-day windows per symbol, and the script walks through them. Symbols closed out completely before the look-back started won't show up. That's fine: their P&L is already in the income history.
- Income in a non-stable asset (rare) is set to 0 USD in the series. Mention it if `summary.json` counts show any.
- Don't report balances in different assets as one sum without converting them. Use the combined base.
