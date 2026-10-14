"""Pull everything needed for a status report + P&L history into one JSON file (read-only).

usage: BN_KEY=... BN_SECRET=... python3 fetch_account.py --env testnet --out data.json [--days 90]
"""
import argparse, json, sys, time
sys.path.insert(0, __import__("os").path.dirname(__file__))
from bn_readonly import ReadOnlyClient

ap = argparse.ArgumentParser()
ap.add_argument("--env", default="testnet", help="testnet | demo | live | full base URL")
ap.add_argument("--out", required=True)
ap.add_argument("--days", type=int, default=90, help="income history look-back (API max ~90 days per query window)")
a = ap.parse_args()

c = ReadOnlyClient(a.env)
now = int(time.time() * 1000); DAY = 86_400_000

# 1. income history (transfers, realised P&L, commission, funding), in 7-day windows, paged
rows, t = {}, now - a.days * DAY
while t < now:
    end = min(t + 7 * DAY, now); s = t
    while True:
        r = c.get("/fapi/v1/income", {"startTime": s, "endTime": end, "limit": 1000})
        for x in r:
            rows[(x["tranId"], x["incomeType"], x["symbol"], x["asset"], x["time"])] = x
        if len(r) < 1000:
            break
        s = r[-1]["time"] + 1
    t = end + 1
income = sorted(rows.values(), key=lambda x: x["time"])

# 2. account snapshot, open orders
acct = c.get("/fapi/v2/account")
positions = [p for p in acct["positions"] if float(p["positionAmt"])]
orders = c.get("/fapi/v1/openOrders")

# 3. fills + 15m closes for every symbol traded or held (to rebuild unrealised P&L over time)
start = income[0]["time"] if income else now - DAY
syms = sorted({x["symbol"] for x in income if x["symbol"] and x["incomeType"] in ("COMMISSION", "REALIZED_PNL")}
              | {p["symbol"] for p in positions})
trades, klines = {}, {}
for i, s in enumerate(syms):
    tr, frm = [], start - 3_600_000
    while True:  # userTrades window is max 7 days per call
        r = c.get("/fapi/v1/userTrades", {"symbol": s, "startTime": frm,
                                           "endTime": min(frm + 7 * DAY - 1, now), "limit": 1000})
        tr += r
        if len(r) == 1000:
            frm = r[-1]["time"] + 1
        elif frm + 7 * DAY < now:
            frm += 7 * DAY
        else:
            break
    trades[s] = tr
    kl, frm = [], start - 900_000
    while frm < now:
        r = c.get("/fapi/v1/klines", {"symbol": s, "interval": "15m", "startTime": frm, "limit": 1500}, signed=False)
        if not r:
            break
        kl += r; frm = r[-1][0] + 900_000
        if len(r) < 1500:
            break
    klines[s] = [(k[0], float(k[4])) for k in kl]
    if i % 25 == 0:
        print(f"symbols {i}/{len(syms)}", file=sys.stderr, flush=True)

json.dump({"env": a.env, "ts": now, "income": income, "assets": acct["assets"],
           "account": {k: v for k, v in acct.items() if k not in ("positions", "assets")},
           "positions": positions, "open_orders": orders, "trades": trades, "klines": klines},
          open(a.out, "w"))
print(f"wrote {a.out}: {len(income)} income rows, {len(positions)} positions, "
      f"{len(orders)} open orders, {sum(len(v) for v in trades.values())} fills, {len(syms)} symbols")
