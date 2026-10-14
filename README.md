# binance-account-monitor

This package lets an agent run read-only status and P&L checks on a Binance USD-M futures account.

- `AGENTS.md`: standing instructions. The agent only reads, uses a combined stablecoin capital base, and produces one simple chart.
- `.agents/skills/binance-account-pnl/SKILL.md`: the procedure, how each number is built, the reply format and known pitfalls.
- `.agents/skills/binance-account-pnl/scripts/`
  - `bn_readonly.py`: a client that only sends GET requests, to a list of read endpoints.
  - `fetch_account.py`: needs the API keys. Pulls income, account, positions, open orders, fills and 15-minute klines into `data.json`.
  - `report.py`: runs offline. Writes `summary.md`, `summary.json` and `pnl_history.png`.

Install: unzip into the agent's home directory (e.g. `/home/user`). If an AGENTS.md is already there, merge this one into it. Requires Python 3 and matplotlib.

Quick run, where `SECRET{NAME}` means the stored secret name wrapped in U+26BF:
    BN_KEY='SECRET{BINANCE_DEMO_API_KEY}' BN_SECRET='SECRET{BINANCE_DEMO_SECRET_KEY}' \
      python3 .agents/skills/binance-account-pnl/scripts/fetch_account.py --env testnet --out run/data.json
    python3 .agents/skills/binance-account-pnl/scripts/report.py run/data.json --outdir run/
