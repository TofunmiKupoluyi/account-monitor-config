"""Status summary + self-contained HTML P&L report from fetch_account.py output. Offline - no network,
no keys, no third-party libraries (stdlib only).

usage: python3 report.py data.json --outdir out/ [--include-nonstable]
writes out/pnl_report.html, out/summary.json, out/summary.md

Capital base = net TRANSFER income per asset over the look-back, summed across stablecoins at 1:1
(USDT + USDC + FDUSD + ...). Assets without a transfer row (e.g. pre-existing balances) are added at
their wallet balance minus their realised income. Non-stable assets (BTC, BNB...) are excluded from the
base unless --include-nonstable, in which case they are valued at the latest <ASSET>USDT 15m close if
available in the data, else they are reported and excluded.

pnl_report.html is one file with no external requests: the cumulative P&L chart (inline SVG with a
hover / arrow-key crosshair), the P&L breakdown, and open positions (best / worst by unrealised % of
entry notional).
"""
import argparse, bisect, collections, datetime as dt, html, json, math, os

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
cap_txt = " + ".join(f"{v:,.0f} {k}" for k, v in cap_rows.items())

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
BREAKDOWN = [("realised_trades", "Closed-trade P&L", 0), ("commission", "Commission", 0), ("other", "Other income", 0),
             ("realised_net", "Realised net", 1), ("funding", "Funding", 0),
             ("unrealised", "Unrealised (exchange mark)", 0), ("total", "Total", 1)]
md_ = [f"**As of {summ['as_of_utc']} UTC** ({D['env']}), history from {summ['history_start_utc']} UTC",
       f"Capital base: {cap_txt} = **{capital:,.2f} USD**" + (f" (excluded: {excluded})" if excluded else ""), "",
       "| | USD | % of capital |", "|---|---|---|"]
for k, lab, em in BREAKDOWN:
    if k == "other" and not pu[k]:
        continue
    md_.append(f"| {f'**{lab}**' if em else lab} | {pu[k]:+,.2f} | {pp[k]:+.2f}% |")
md_ += ["", f"Positions: {po['n']} ({po['long']} long / {po['short']} short), long {po['long_usd']:,.0f}, "
        f"short {po['short_usd']:,.0f}, net {po['net_usd']:+,.0f}, gross {po['gross_usd']:,.0f} "
        f"({po['gross_over_margin_balance']:.1f}x margin balance). Open orders: {summ['open_orders']}."]
open(os.path.join(a.outdir, "summary.md"), "w").write("\n".join(md_) + "\n")

# ================================ HTML report ================================
esc = html.escape
SERIES = [("Total", total), ("Unrealised", S["unrealised"]),
          ("Realised (trades − fees)", realised), ("Funding", S["funding"])]
PC = [[round(pct(v), 4) for v in vals] for _, vals in SERIES]
ymin = min(0.0, min(min(p) for p in PC)); ymax = max(0.0, max(max(p) for p in PC))
if ymax - ymin < 1e-9: ymax = ymin + 1
pad = (ymax - ymin) * 0.07; ymin -= pad; ymax += pad
W, H, ML, MR, MT, MB = 960, 380, 66, 14, 12, 30
X = lambda t: ML + (W - ML - MR) * (t - grid[0]) / max(grid[-1] - grid[0], 1)
Y = lambda v: MT + (H - MT - MB) * (ymax - v) / (ymax - ymin)

def nice_step(span, n=5):
    raw = span / max(n, 1); mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag)

svg = []
step = nice_step(ymax - ymin)
dec = 0 if step >= 1 else (1 if step >= 0.1 else 2)
v = math.ceil(ymin / step) * step
while v <= ymax + 1e-9:  # horizontal gridlines + y tick labels
    y = Y(v)
    svg.append(f'<line class="gl" x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}"/>')
    svg.append(f'<text x="{ML-8}" y="{y:.1f}" dy="4" text-anchor="end">{"0%" if abs(v) < 1e-9 else f"{v:+.{dec}f}%"}</text>')
    v += step
span = grid[-1] - grid[0]
xstep = 86_400_000 if span > 2 * 86_400_000 else 21_600_000
xt = list(range(grid[0] - grid[0] % xstep + xstep, grid[-1] + 1, xstep))
for t in xt[::max(1, math.ceil(len(xt) / 8))]:
    svg.append(f'<text x="{X(t):.1f}" y="{H-MB+18}" text-anchor="middle">'
               f'{T(t):%m-%d}{"" if xstep == 86_400_000 else f" {T(t):%H:%M}"}</text>')
if ymin < 0 < ymax:
    svg.append(f'<line class="ax" x1="{ML}" y1="{Y(0):.1f}" x2="{W-MR}" y2="{Y(0):.1f}"/>')
for i in range(len(SERIES) - 1, -1, -1):  # draw back-to-front so Total sits on top
    pts = " ".join(f"{X(t):.1f},{Y(p):.1f}" for t, p in zip(grid, PC[i]))
    svg.append(f'<polyline class="ln" style="stroke:var(--s{i+1})" points="{pts}"/>')
for i, p in enumerate(PC):
    svg.append(f'<circle class="dot" style="fill:var(--s{i+1})" cx="{X(grid[-1]):.1f}" cy="{Y(p[-1]):.1f}" r="4"/>')
svg.append(f'<line id="xh" class="xh" y1="{MT}" y2="{H-MB}" x1="0" x2="0"/>')
for i in range(len(SERIES)):
    svg.append(f'<circle id="hd{i}" class="hdot" style="fill:var(--s{i+1})" r="4"/>')

def sign_cls(v): return "up" if v > 0 else ("dn" if v < 0 else "")
def bar(v, vmax):  # diverging inline bar around a centre baseline
    w = 0 if vmax <= 0 else min(50.0, 50.0 * abs(v) / vmax)
    return f'<div class="bcell"><div class="b {"pos" if v >= 0 else "neg"}" style="width:{w:.2f}%"></div></div>'

tiles = "".join(
    f'<div class="tile"><div class="lb">{esc(lab)}</div><div class="v {sign_cls(pu[k])}">{pp[k]:+.2f}%</div>'
    f'<div class="sub">{pu[k]:+,.2f} USD</div></div>'
    for k, lab in [("total", "Total P&L"), ("unrealised", "Unrealised"), ("realised_net", "Realised net"), ("funding", "Funding")])

legend = "".join(
    f'<span><span class="key" style="background:var(--s{i+1})"></span>{esc(n)} <b>{PC[i][-1]:+.2f}%</b> '
    f'<span class="dim">({vals[-1]:+,.0f})</span></span>' for i, (n, vals) in enumerate(SERIES))

bmax = max(abs(pp[k]) for k, _, _ in BREAKDOWN) or 1
btab = "".join(
    f'<tr class="{"em" if em else ""}"><td>{esc(lab)}</td><td class="r">{pu[k]:+,.2f}</td>'
    f'<td class="r">{pp[k]:+.2f}%</td><td>{bar(pp[k], bmax)}</td></tr>'
    for k, lab, em in BREAKDOWN if not (k == "other" and not pu[k]))

def upct(p):
    en = abs(float(p["positionAmt"])) * float(p["entryPrice"])
    return (100 * float(p["unrealizedProfit"]) / en) if en else 0.0
ranked = sorted(P, key=upct, reverse=True)
shown = ranked if len(ranked) <= 24 else ranked[:12] + [None] + ranked[-12:]
pmax = max((abs(upct(p)) for p in shown if p), default=1) or 1
prows = []
for p in shown:
    if p is None:
        prows.append(f'<tr class="skip"><td colspan="6">… {len(ranked)-24} more between …</td></tr>'); continue
    amt, en, u = float(p["positionAmt"]), abs(float(p["positionAmt"])) * float(p["entryPrice"]), float(p["unrealizedProfit"])
    prows.append(f'<tr><td>{esc(p["symbol"])}</td><td>{"long" if amt > 0 else "short"}</td><td class="r">{en:,.0f}</td>'
                 f'<td class="r">{u:+,.2f}</td><td class="r">{upct(p):+.2f}%</td><td>{bar(upct(p), pmax)}</td></tr>')
ptab = ("".join(prows)) or '<tr><td colspan="6">No open positions.</td></tr>'
ups = sum(float(p["unrealizedProfit"]) > 0 for p in P)

day_rows, seen = [], set()
for i in range(len(grid) - 1, -1, -1):  # last grid point of each UTC day
    d = T(grid[i]).date()
    if d not in seen:
        seen.add(d)
        day_rows.append(f'<tr><td>{d}</td>' + "".join(f'<td class="r">{p[i]:+.2f}%</td>' for p in PC) + "</tr>")
dtab = "".join(reversed(day_rows))

LIGHT = ("color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;"
         "--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;"
         "--s4:#eda100;--pos:#2a78d6;--neg:#e34948;--good:#006300;--bad:#d03b3b")
DARK = ("color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;"
        "--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;"
        "--s4:#c98500;--pos:#3987e5;--neg:#e66767;--good:#0ca30c;--bad:#d03b3b")
CSS = ("body{" + LIGHT + "}"
       "@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])) body{" + DARK + "}}"
       ":root[data-theme=dark] body{" + DARK + "}" + """
body{margin:0;background:var(--plane);color:var(--ink);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:20px 16px 32px}
h1{font-size:19px;margin:0}
.head .sub,.card .sub{color:var(--muted);font-size:12.5px;margin:2px 0 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:16px}
.tile,.card{background:var(--surface);border:1px solid var(--border);border-radius:10px}
.tile{padding:12px 14px}
.tile .lb{font-size:12.5px;color:var(--ink2)}
.tile .v{font-size:25px;font-weight:600;margin-top:2px}
.tile .sub{font-size:12.5px;color:var(--muted)}
.v.up{color:var(--good)}.v.dn{color:var(--bad)}
.card{padding:16px 18px;margin-top:16px}
.card h2{font-size:15px;margin:0}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 2px;font-size:13px}
.legend .key,.tt-key{display:inline-block;width:16px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.legend b,.t td,#pnlsvg text,.tt-row b,.tt-sub{font-variant-numeric:tabular-nums}
.dim{color:var(--muted)}
#chartwrap{position:relative;outline:none;margin-top:4px}
#chartwrap:focus-visible{outline:2px solid var(--axis);outline-offset:2px;border-radius:6px}
#pnlsvg{display:block;width:100%;height:auto}
#pnlsvg text{fill:var(--muted);font-size:11.5px}
#pnlsvg .gl{stroke:var(--grid)}
#pnlsvg .ax,#pnlsvg .xh{stroke:var(--axis)}
#pnlsvg .xh,#pnlsvg .hdot{visibility:hidden}
#pnlsvg .ln{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
#pnlsvg .dot,#pnlsvg .hdot{stroke:var(--surface);stroke-width:2}
#tip{position:absolute;top:16px;left:0;visibility:hidden;background:var(--surface);border:1px solid var(--border);
 border-radius:8px;padding:8px 10px;pointer-events:none;box-shadow:0 2px 10px rgba(0,0,0,.14);font-size:12.5px;
 white-space:nowrap;z-index:2}
.tt-time{color:var(--muted);margin-bottom:4px}
.tt-row{display:flex;align-items:center;margin-top:2px}
.tt-sub{color:var(--ink2);margin-left:5px}
.tt-name{color:var(--muted);margin-left:7px}
.tscroll{overflow-x:auto;margin-top:8px}
.t{border-collapse:collapse;width:100%}
.t th{font-weight:600;color:var(--ink2);font-size:12.5px;text-align:left;padding:5px 10px;border-bottom:1px solid var(--grid)}
.t th.r,.t td.r{text-align:right}
.t td{padding:5px 10px;border-bottom:1px solid var(--grid)}
.t tr:last-child td{border-bottom:none}
.t tr.em td{font-weight:650}
.t tr.skip td{color:var(--muted);text-align:center;font-size:12.5px}
.bcell{position:relative;height:14px;min-width:120px}
.bcell::before{content:"";position:absolute;left:50%;top:-1px;bottom:-1px;width:1px;background:var(--axis)}
.b{position:absolute;top:0;height:100%}
.b.pos{left:50%;background:var(--pos);border-radius:0 4px 4px 0}
.b.neg{right:50%;background:var(--neg);border-radius:4px 0 0 4px}
details.card summary{cursor:pointer;font-size:13.5px;color:var(--ink2)}
.notes{color:var(--muted);font-size:12.5px;margin-top:14px}
""")
JS = """
(function(){
const D=__D__,svg=document.getElementById('pnlsvg'),wrap=document.getElementById('chartwrap'),
tip=document.getElementById('tip'),xh=document.getElementById('xh'),
hd=D.names.map((_,i)=>document.getElementById('hd'+i)),t0=D.t[0],tN=D.t[D.t.length-1];
const px=t=>D.ml+(D.w-D.ml-D.mr)*(t-t0)/Math.max(tN-t0,1);
const py=v=>D.mt+(D.h-D.mt-D.mb)*(D.ymax-v)/(D.ymax-D.ymin);
let idx=-1;
const fmt=(v,d)=>(v>=0?'+':'')+v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
function show(i){idx=i;const t=D.t[i],x=px(t);
 xh.setAttribute('x1',x);xh.setAttribute('x2',x);xh.style.visibility='visible';
 D.s.forEach((s,k)=>{hd[k].setAttribute('cx',x);hd[k].setAttribute('cy',py(s[i]));hd[k].style.visibility='visible';});
 tip.replaceChildren();
 const h=document.createElement('div');h.className='tt-time';
 h.textContent=new Date(t).toISOString().slice(0,16).replace('T',' ')+' UTC';tip.appendChild(h);
 D.s.forEach((s,k)=>{const r=document.createElement('div');r.className='tt-row';
  const key=document.createElement('span');key.className='tt-key';key.style.background='var(--s'+(k+1)+')';
  const val=document.createElement('b');val.textContent=fmt(s[i],2)+'%';
  const usd=document.createElement('span');usd.className='tt-sub';usd.textContent=fmt(s[i]*D.cap/100,0);
  const nm=document.createElement('span');nm.className='tt-name';nm.textContent=D.names[k];
  r.append(key,val,usd,nm);tip.appendChild(r);});
 tip.style.visibility='visible';
 const sw=wrap.getBoundingClientRect().width,cx=x*sw/D.w,tw=tip.offsetWidth;
 tip.style.left=(cx+12+tw>sw?Math.max(cx-tw-12,0):cx+12)+'px';}
function hide(){idx=-1;tip.style.visibility='hidden';xh.style.visibility='hidden';
 hd.forEach(d=>d.style.visibility='hidden');}
svg.addEventListener('pointermove',e=>{const r=svg.getBoundingClientRect(),
 vt=t0+((e.clientX-r.left)*D.w/r.width-D.ml)*(tN-t0)/(D.w-D.ml-D.mr);
 let lo=0,hi=D.t.length-1;while(hi-lo>1){const m=(lo+hi)>>1;if(D.t[m]<vt)lo=m;else hi=m;}
 show(vt-D.t[lo]<=D.t[hi]-vt?lo:hi);});
svg.addEventListener('pointerleave',hide);
wrap.addEventListener('keydown',e=>{if(e.key==='Escape')return hide();
 const n=D.t.length-1;let i=idx<0?n:idx;
 if(e.key==='ArrowLeft')i=Math.max(0,i-1);else if(e.key==='ArrowRight')i=Math.min(n,i+1);
 else if(e.key==='Home')i=0;else if(e.key==='End')i=n;else return;
 e.preventDefault();show(i);});
wrap.addEventListener('focus',()=>{if(idx<0)show(D.t.length-1);});
wrap.addEventListener('blur',hide);
})();
"""
data = {"t": grid, "s": PC, "names": [n for n, _ in SERIES], "cap": capital,
        "ymin": ymin, "ymax": ymax, "w": W, "h": H, "ml": ML, "mr": MR, "mt": MT, "mb": MB}
env = esc(D["env"])
page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Futures P&L ({env})</title><style>{CSS}</style></head>
<body><div class="wrap">
<div class="head"><h1>Binance USD-M futures — {env.upper()}</h1>
<p class="sub">{T(start):%Y-%m-%d %H:%M} → {T(now):%Y-%m-%d %H:%M} UTC · capital base {esc(cap_txt)} = {capital:,.0f} USD{(" · excluded from base: " + esc(", ".join(f"{k} {v:,.4g}" for k, v in excluded.items()))) if excluded else ""}</p></div>
<div class="tiles">{tiles}</div>
<div class="card"><h2>Cumulative P&L</h2>
<p class="sub">% of capital · 15-minute grid · hover, or focus and use ← → for values</p>
<div class="legend">{legend}</div>
<div id="chartwrap" tabindex="0" role="application" aria-label="Cumulative P&L chart; use arrow keys to step through points">
<svg id="pnlsvg" viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative P&L as percent of capital">{"".join(svg)}</svg>
<div id="tip"></div></div></div>
<div class="card"><h2>P&L breakdown</h2><p class="sub">Window totals, USD and % of capital</p>
<div class="tscroll"><table class="t"><thead><tr><th>Component</th><th class="r">USD</th><th class="r">% of capital</th><th></th></tr></thead>
<tbody>{btab}</tbody></table></div></div>
<div class="card"><h2>Open positions</h2>
<p class="sub">{po["n"]} positions ({ups} up / {po["n"] - ups} down · {po["long"]} long / {po["short"]} short) · net {po["net_usd"]:+,.0f} · gross {po["gross_usd"]:,.0f}{f" ({po['gross_over_margin_balance']:.1f}× margin balance)" if po["gross_over_margin_balance"] else ""} · open orders {summ["open_orders"]}{"" if len(ranked) <= 24 else " · showing best 12 / worst 12 by unrealised %"}</p>
<div class="tscroll"><table class="t"><thead><tr><th>Symbol</th><th>Side</th><th class="r">Entry notional</th><th class="r">uPnL USD</th><th class="r">uPnL % of entry</th><th></th></tr></thead>
<tbody>{ptab}</tbody></table></div></div>
<details class="card"><summary>Chart data (daily closes)</summary>
<div class="tscroll"><table class="t"><thead><tr><th>Day (UTC)</th><th class="r">Total</th><th class="r">Unrealised</th><th class="r">Realised</th><th class="r">Funding</th></tr></thead>
<tbody>{dtab}</tbody></table></div></details>
<p class="notes">All figures from the exchange API. Unrealised history is rebuilt from fills × 15-minute closes and the
last point is anchored to the exchange's totalUnrealizedProfit (rebuilt {rebuilt_last_u:+,.2f} vs exchange {S["unrealised"][-1]:+,.2f} USD).
Not investment advice.</p>
</div><script>{JS.replace("__D__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))}</script></body></html>
"""
open(os.path.join(a.outdir, "pnl_report.html"), "w").write(page)
print("\n".join(md_))
print(f"\nwrote {a.outdir}/pnl_report.html, summary.json, summary.md")
