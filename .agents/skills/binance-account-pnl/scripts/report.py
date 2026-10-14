"""Status summary + P&L history chart from fetch_account.py output. Offline - no network, no keys.

usage: python3 report.py data.json --outdir out/ [--include-nonstable]
writes out/pnl_history.png, out/summary.json, out/summary.md

Capital base = net TRANSFER income per asset over the look-back, summed across stablecoins at 1:1
(USDT + USDC + FDUSD + ...). Assets without a transfer row (e.g. pre-existing balances) are added at
their wallet balance minus their realised income. Non-stable assets (BTC, BNB...) are excluded from the
base unless --include-nonstable, in which case they are valued at the latest <ASSET>USDT 15m close if
available in the data, else they are reported and excluded.
"""
import argparse, bisect, collections, datetime as dt, json, os
import matplotlib; matplotlib.use("Agg")
import matplotlib.dates as md, matplotlib.pyplot as plt, matplotlib.ticker as mt

STABLES = {"USDT", "USDC", "FDUSD", "BUSD", "USDE", "TUSD", "DAI"}
ap = argparse.ArgumentParser()
ap.add_argument("data"); ap.add_argument("--outdir", required=True)
ap.add_argument("--include-nonstable", action="store_true")
a = ap.parse_args(); os.makedirs(a.outdir, exist_ok=True)
D = json.load(open(a.data))
I, TR, KL, now = D["income"], D["trades"], D["klines"], D["ts"]
A = D["account"]; assets = {x["asset"]: x for x in D["assets"]}
T = lambda ms: dt.datetime.utcfromtimestamp(ms / 1000)

# ---- capital base (combined across assets) ----
inc_by = collections.defaultdict(lambda: collections.defaultdict(float))
for x in I:
    inc_by[x["asset"]][x["incomeType"]] += float(x["income"])
capital, cap_rows, excluded = 0.0, {}, {}
for k, x in assets.items():
    wb = float(x["walletBalance"])
    if not wb and k not in inc_by:
        continue
    dep = inc_by[k].get("TRANSFER", 0.0)
    if not dep:  # balance that pre-dates the look-back: back out income booked since
        dep = wb - sum(v for t_, v in inc_by[k].items() if t_ != "TRANSFER")
    if k in STABLES:
        cap_rows[k] = dep; capital += dep
    elif a.include_nonstable and f"{k}USDT" in KL and KL[f"{k}USDT"]:
        usd = dep * KL[f"{k}USDT"][-1][1]; cap_rows[k] = usd; capital += usd
    else:
        excluded[k] = wb
if capital <= 0:
    raise SystemExit("could not determine a positive capital base")

# ---- rebuild cumulative series on a 15m grid ----
start = I[0]["time"]
grid = list(range(start - start % 900_000 + 900_000, now, 900_000)) + [now]
fills = sorted(((f["time"], s, (1 if f["side"] == "BUY" else -1) * float(f["qty"]), float(f["price"]))
                for s, v in TR.items() for f in v), key=lambda z: z[0])
kt = {s: [k[0] for k in v] for s, v in KL.items()}
def close_at(s, t):  # last 15m bar fully closed at t (open time <= t - 15m)
    i = bisect.bisect_right(kt.get(s, []), t - 900_000) - 1
    return KL[s][i][1] if i >= 0 else None
inc = sorted((x["time"], x["incomeType"], float(x["income"]) * (1 if x["asset"] in STABLES else 0))
             for x in I if x["incomeType"] != "TRANSFER")
c = collections.defaultdict(float); qty, cost = {}, {}
S = {"realised_trades": [], "commission": [], "funding": [], "other": [], "unrealised": []}
fi = ii = 0
for t in grid:
    while fi < len(fills) and fills[fi][0] <= t:
        _, s, q, p = fills[fi]; qty[s] = qty.get(s, 0) + q; cost[s] = cost.get(s, 0) + q * p; fi += 1
    while ii < len(inc) and inc[ii][0] <= t:
        c[inc[ii][1]] += inc[ii][2]; ii += 1
    price_pnl = sum(qty[s] * (close_at(s, t) or 0) - cost[s] for s in qty)
    S["realised_trades"].append(c["REALIZED_PNL"]); S["commission"].append(c["COMMISSION"])
    S["funding"].append(c["FUNDING_FEE"])
    S["other"].append(sum(v for k, v in c.items() if k not in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")))
    S["unrealised"].append(price_pnl - c["REALIZED_PNL"])
rebuilt_last_u = S["unrealised"][-1]
S["unrealised"][-1] = float(A["totalUnrealizedProfit"])  # anchor final point to exchange mark-price uPnL
realised = [r + m + o for r, m, o in zip(S["realised_trades"], S["commission"], S["other"])]
total = [r + f + u for r, f, u in zip(realised, S["funding"], S["unrealised"])]
pct = lambda v: 100 * v / capital

# ---- chart: one panel, 4 lines ----
x = [T(t) for t in grid]
fig, ax = plt.subplots(figsize=(13, 6.5))
ax.plot(x, [pct(v) for v in total], c="k", lw=2.4, label=f"Total  {pct(total[-1]):+.2f}%  ({total[-1]:+,.0f})")
ax.plot(x, [pct(v) for v in S["unrealised"]], c="tab:purple", lw=1.6,
        label=f"Unrealised  {pct(S['unrealised'][-1]):+.2f}%  ({S['unrealised'][-1]:+,.0f})")
ax.plot(x, [pct(v) for v in realised], c="tab:blue", lw=1.6,
        label=f"Realised (trades − fees)  {pct(realised[-1]):+.2f}%  ({realised[-1]:+,.0f})")
ax.plot(x, [pct(v) for v in S["funding"]], c="tab:green", lw=1.6, ls="--",
        label=f"Funding  {pct(S['funding'][-1]):+.2f}%  ({S['funding'][-1]:+,.0f})")
ax.axhline(0, c="grey", lw=.7); ax.grid(alpha=.3); ax.legend(loc="upper left", fontsize=10)
cap_txt = " + ".join(f"{v:,.0f} {k}" for k, v in cap_rows.items())
ax.set_ylabel(f"% of capital ({cap_txt} = {capital:,.0f} USD)")
ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:+.2f}%"))
ax.xaxis.set_major_formatter(md.DateFormatter("%m-%d %H:%M" if now - start < 10 * 86_400_000 else "%Y-%m-%d"))
ax.set_title(f"Binance USD-M futures ({D['env']}) — cumulative P&L, {T(start):%Y-%m-%d %H:%M} → {T(now):%Y-%m-%d %H:%M} UTC")
fig.tight_layout(); fig.savefig(os.path.join(a.outdir, "pnl_history.png"), dpi=110)

# ---- summary ----
P = D["positions"]
notional = [float(p["notional"]) for p in P]
L = sum(n for n in notional if n > 0); Sh = sum(n for n in notional if n < 0)
by_u = sorted(P, key=lambda p: float(p["unrealizedProfit"]))
summ = {
    "env": D["env"], "as_of_utc": str(T(now)), "history_start_utc": str(T(start)),
    "capital_base": cap_rows, "capital_total_usd": capital, "excluded_from_base": excluded,
    "wallet": {k: float(x["walletBalance"]) for k, x in assets.items() if float(x["walletBalance"])},
    "account": {k: float(A[k]) for k in ("totalWalletBalance", "totalUnrealizedProfit", "totalMarginBalance",
                                         "totalInitialMargin", "totalMaintMargin", "availableBalance")},
    "pnl_usd": {"realised_trades": S["realised_trades"][-1], "commission": S["commission"][-1],
                "other": S["other"][-1], "realised_net": realised[-1], "funding": S["funding"][-1],
                "unrealised": S["unrealised"][-1], "total": total[-1]},
    "unrealised_rebuilt_last_vs_exchange": [rebuilt_last_u, S["unrealised"][-1]],
    "unrealised_range_usd": [min(S["unrealised"]), max(S["unrealised"])],
    "positions": {"n": len(P), "long": sum(n > 0 for n in notional), "short": sum(n < 0 for n in notional),
                  "long_usd": L, "short_usd": Sh, "net_usd": L + Sh, "gross_usd": L - Sh,
                  "gross_over_margin_balance": (L - Sh) / float(A["totalMarginBalance"]) if float(A["totalMarginBalance"]) else None,
                  "worst_upnl": [(p["symbol"], float(p["unrealizedProfit"])) for p in by_u[:3]],
                  "best_upnl": [(p["symbol"], float(p["unrealizedProfit"])) for p in by_u[-3:][::-1]],
                  "largest": [(p["symbol"], float(p["notional"])) for p in sorted(P, key=lambda p: -abs(float(p["notional"])))[:5]]},
    "open_orders": len(D.get("open_orders", [])),
    "counts": dict(collections.Counter(x["incomeType"] for x in I)), "fills": len(fills),
}
summ["pnl_pct_of_capital"] = {k: pct(v) for k, v in summ["pnl_usd"].items()}
json.dump(summ, open(os.path.join(a.outdir, "summary.json"), "w"), indent=1)
pu, pp = summ["pnl_usd"], summ["pnl_pct_of_capital"]; po = summ["positions"]
md_ = [f"**As of {summ['as_of_utc']} UTC** ({D['env']}), history from {summ['history_start_utc']} UTC",
       f"Capital base: {cap_txt} = **{capital:,.2f} USD**" + (f" (excluded: {excluded})" if excluded else ""), "",
       "| | USD | % of capital |", "|---|---|---|"]
for k, lab in [("realised_trades", "Closed-trade P&L"), ("commission", "Commission"), ("other", "Other income"),
               ("realised_net", "**Realised net**"), ("funding", "Funding"), ("unrealised", "Unrealised (exchange mark)"),
               ("total", "**Total**")]:
    if k == "other" and not pu[k]:
        continue
    md_.append(f"| {lab} | {pu[k]:+,.2f} | {pp[k]:+.2f}% |")
md_ += ["", f"Positions: {po['n']} ({po['long']} long / {po['short']} short), long {po['long_usd']:,.0f}, "
        f"short {po['short_usd']:,.0f}, net {po['net_usd']:+,.0f}, gross {po['gross_usd']:,.0f} "
        f"({po['gross_over_margin_balance']:.1f}x margin balance). Open orders: {summ['open_orders']}."]
open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(md_) + "\n")
print("\n".join(md_))
