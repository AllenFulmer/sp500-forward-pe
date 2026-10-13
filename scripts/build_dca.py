#!/usr/bin/env python3
"""Monthly DCA into last month's best S&P 500 stock: data/dca.json, data/dca_lots.csv, data/dca_coverage.csv.

Idea credit: Michael Luo (@AzianMike), https://github.com/azianmike/monthly-winner-research
This is an independent rebuild:
- Membership: fja05680/sp500 historical components, as of the signal month's last trading day.
- Prices: Yahoo Finance daily Close / Adj Close (dividend- and split-adjusted) via yfinance.
- Signal: total return over the prior calendar month = AdjClose(last trading day of M) / AdjClose(last trading day of M-1) - 1.
  A member is ranked only if both prices exist. Members with no Yahoo history are "missing" (counted, never filled).
- Buy: close of the first trading day of month M+1 (after the signal is known: no lookahead). $1,000 per month.
- Hold forever. A holding whose Yahoo history ends before the last date is carried at its last close (as if the
  acquisition cash / final price were held at 0%), and flagged. Sensitivity: the "zero rule" of the original study.
- Benchmark: $1,000 per month into the S&P 500 Total Return index (^SP500TR) at the same buy-day close.
- Extras: top 5 at $200 each; "12-month winner" (best trailing 12-month total return) at $1,000.
Usage: python scripts/build_dca.py [path/to/px.pkl]   (pickle of yfinance download with Open/Close/Adj Close)
"""
import csv, json, os, sys, pickle, datetime as dt
import pandas as pd, numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PX = sys.argv[1] if len(sys.argv) > 1 else "/workspace/dca/px.pkl"
COMP = "/workspace/mom/comp.csv"  # https://github.com/fja05680/sp500 (S&P 500 Historical Components & Changes)
FIRST_BUY = "1997-01"

d = pickle.load(open(PX, "rb"))
A, C = d["Adj Close"], d["Close"]
idx = A["^SP500TR"].dropna().index                       # trading calendar
last = idx[-1]
me = pd.Series(idx, index=idx).groupby(idx.to_period("M")).max()   # last trading day per month
fd = pd.Series(idx, index=idx).groupby(idx.to_period("M")).min()   # first trading day per month
comp = pd.read_csv(COMP, parse_dates=["date"])
comp_last = comp.date.max()
def members(day):
    r = comp[comp.date <= day].iloc[-1]
    return [t.replace(".", "-") for t in r.tickers.split(",")]

# ---- identity / data-quality screens (ticker recycling) ----
ZERO_MAX = 0.30   # max share of unchanged daily closes over the trailing year; S&P 500 members are liquid
zero_share = (C.diff() == 0).where(C.notna()).rolling(252, min_periods=120).mean()
first_px = A.apply(lambda s: s.first_valid_index())
spell = {}   # (ticker) -> list of (spell_start, spell_end) from consecutive component snapshots
prev = set()
for _, r in comp.iterrows():
    cur = {t.replace(".", "-") for t in r.tickers.split(",")}
    for t in cur - prev: spell.setdefault(t, []).append([r.date, None])
    for t in prev - cur: spell[t][-1][1] = r.date
    prev = cur
def spell_start(t, day):
    for a, b in spell.get(t, []):
        if a <= day and (b is None or day < b): return a
    return None
# A Yahoo history that starts after the current membership spell began belongs to a later owner of the symbol
# (or is truncated); such ticker-months are screened out and counted.
def late_history(t, day):
    a = spell_start(t, day); f = first_px.get(t)
    return t not in SAME_CO and a is not None and f is not None and f > max(a, pd.Timestamp("1995-11-30")) + pd.Timedelta(days=31)
RECYCLED = set()
SAME_CO = {"AEE", "FE"}  # Ameren and FirstEnergy were formed by 1997 mergers; Yahoo history starts at formation
# Found by manual review of selected names (series spliced from a different, illiquid company; see notes in dca.json):
RECYCLED |= {"EP", "MCIC", "CPWR", "SNT"}
# Johnbrick123/sp500-data recycled_tickers.csv and quarantine list
RECYCLED |= {"ABX", "APC", "BBBY", "CR", "INFO", "IR", "NE", "NFX", "S", "SBNY", "SHLD", "STI", "TEN", "UN"}

periods = [p for p in me.index if str(p) >= FIRST_BUY]
lots = {"w": [], "t5": [], "w12": [], "sp": []}
coverage, flags = [], []
for p in periods:
    sig = p - 1; m_end, m_prev, m_12 = me[sig], me[sig - 1], me[sig - 12]
    buy = fd[p]
    mem = members(m_end)
    late = [t for t in mem if t in A.columns and late_history(t, m_end)]
    have = [t for t in mem if t in A.columns and t not in RECYCLED and t not in late]
    zs = zero_share.loc[m_end].reindex(have)
    illiquid = [t for t in have if zs.get(t, 0) > ZERO_MAX]
    have = [t for t in have if t not in illiquid]
    a1, a0, a12 = A.loc[m_end, have], A.loc[m_prev, have], A.loc[m_12, have]
    r1 = (a1 / a0 - 1).dropna(); r12 = (a1 / a12 - 1).dropna()
    missing = sorted(set(mem) - set(r1.index))
    r1 = r1.sort_values(ascending=False, kind="mergesort"); r12 = r12.sort_values(ascending=False, kind="mergesort")
    coverage.append(dict(month=str(sig), members=len(mem), ranked=len(r1), missing=len(missing), screened=" ".join(sorted(set(illiquid) | set(late) | (set(mem) & RECYCLED))), missing_tickers=" ".join(missing)))
    def add(key, t, amt, ret):
        s = A[t].loc[buy:].dropna()
        if s.empty: flags.append(f"{p} {key} {t}: no price on/after buy day"); return
        if s.index[0] != buy: flags.append(f"{p} {key} {t}: no close on {buy.date()}, bought {s.index[0].date()}")
        lots[key].append(dict(buy_month=str(p), buy_date=str(s.index[0].date()), signal_month=str(sig), ticker=t, amount=amt,
                              signal_ret=float(ret), buy_px=float(s.iloc[0])))
    add("w", r1.index[0], 1000, r1.iloc[0])
    for t in r1.index[:5]: add("t5", t, 200, r1[t])
    add("w12", r12.index[0], 1000, r12.iloc[0])
    lots["sp"].append(dict(buy_month=str(p), buy_date=str(buy.date()), signal_month=str(sig), ticker="^SP500TR", amount=1000,
                           signal_ret=None, buy_px=float(A.loc[buy, "^SP500TR"])))

# valuation points: every month-end from first buy month, plus the last close
vp = [me[p] for p in periods if me[p] < last] + [last]
labels = [x.strftime("%Y-%m-%d") for x in vp]
held = sorted({l["ticker"] for k in lots for l in lots[k]})
prices, ended = {}, {}
for t in held:
    s = A[t].dropna(); end = s.index[-1]
    if end < last: ended[t] = str(end.date())
    v = A[t].ffill().reindex(vp)
    prices[t] = [None if pd.isna(x) else float(f"{x:.6g}") for x in v]
def value_path(L, zero=False):
    out = np.zeros(len(vp)); inv = np.zeros(len(vp))
    for l in L:
        b = pd.Timestamp(l["buy_date"]); s = A[l["ticker"]].ffill()
        for j, day in enumerate(vp):
            if day < b: continue
            inv[j] += l["amount"]
            if zero and l["ticker"] in ended and day > pd.Timestamp(ended[l["ticker"]]): continue
            out[j] += l["amount"] * s.loc[day] / l["buy_px"]
    return out, inv
def xirr(L, final):
    cf = [(pd.Timestamp(l["buy_date"]), -l["amount"]) for l in L] + [(last, final)]
    t0 = cf[0][0]; ts = [((d - t0).days / 365.25, c) for d, c in cf]
    lo, hi = -0.99, 10
    for _ in range(200):
        mid = (lo + hi) / 2; npv = sum(c / (1 + mid) ** t for t, c in ts)
        lo, hi = (mid, hi) if npv > 0 else (lo, mid)
    return mid
summary = {}
for start in ("1997", "1998"):
    for k in lots:
        L = [l for l in lots[k] if l["buy_month"] >= f"{start}-01"]
        v, inv = value_path(L); vz, _ = value_path(L, zero=True)
        summary[f"{k}_{start}"] = dict(invested=float(inv[-1]), ending=round(float(v[-1]), 2), xirr=round(xirr(L, v[-1]), 4),
                                       ending_zero_rule=round(float(vz[-1]), 2), lots=len(L))
# lot detail for the main strategy
rows = []
for k in ("w", "t5", "w12"):
    for l in lots[k]:
        t = l["ticker"]; fin = A[t].ffill().loc[last]
        l["value"] = round(l["amount"] * fin / l["buy_px"], 2); l["ended"] = ended.get(t, "")
        rows.append(dict(strategy=k, **l))
with open(os.path.join(ROOT, "data", "dca_lots.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(os.path.join(ROOT, "data", "dca_coverage.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(coverage[0].keys())); w.writeheader(); w.writerows(coverage)
# Original study's picks and headline numbers (for side-by-side comparison only)
LUO = "https://raw.githubusercontent.com/azianmike/monthly-winner-research/main/data/"
import urllib.request, io
luo_rows = list(csv.DictReader(io.StringIO(urllib.request.urlopen(LUO + "purchase_ledger.csv").read().decode())))
luo_sum = json.load(urllib.request.urlopen(LUO + "original_summary.json"))
luo = dict(picks={r["buy_date"][:7]: [r["ticker"], round(float(r["previous_month_return"]), 4), round(float(r["ending_marked_value"]), 2)] for r in luo_rows},
           ending=round(luo_sum["strategy_marked_value"], 2), spy=round(luo_sum["spy_value"], 2), xirr=luo_sum["strategy_xirr"], spy_xirr=luo_sum["spy_xirr"],
           as_of=luo_sum["as_of"], start=luo_sum["start"], deposits=luo_sum["purchases"], writeoff_lots=luo_sum["writeoff_lots"],
           missing_ranking_months=luo_sum["missing_ranking_months"])
out = dict(luo=luo, generated=dt.date.today().isoformat(), last_close=str(last.date()), first_buy=FIRST_BUY, membership_through=str(comp_last.date()),
           labels=labels, prices=prices, ended=ended,
           lots={k: [[l["buy_date"], l["ticker"], l["amount"], float(f'{l["buy_px"]:.6g}'), None if l["signal_ret"] is None else round(l["signal_ret"], 4), l["signal_month"]] for l in lots[k]] for k in lots},
           coverage=[[c["month"], c["members"], c["ranked"]] for c in coverage], flags=flags, recycled=sorted(RECYCLED), summary=summary,
           sources=dict(membership="https://github.com/fja05680/sp500", prices="https://finance.yahoo.com/ (yfinance, Adj Close)",
                        idea="https://github.com/azianmike/monthly-winner-research"))
json.dump(out, open(os.path.join(ROOT, "data", "dca.json"), "w"), separators=(",", ":"))
for k, v in summary.items(): print(k, v)
print("flags", flags[:20], len(flags)); print("ended", len(ended))
