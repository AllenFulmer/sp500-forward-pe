#!/usr/bin/env python3
"""Rebuild data/momentum_winners.csv and data/momentum.json for momentum.html.

Winners are the top calendar-year PRICE return among stocks that were S&P 500 members at the
END of that year (the convention used by S&P Dow Jones Indices and year-end press lists).
Winners and returns for live tickers are recomputed from Yahoo Finance (yfinance); for
delisted/acquired names the values are hard-coded below with their sources.
Strategy returns use TOTAL return (Yahoo 'Adj Close', or Exa total-return index for delisted names).
Benchmark: S&P 500 Total Return index (^SP500TR, Yahoo).
Requires: pip install yfinance pandas.   Run: python3 scripts/build_momentum.py
"""
import csv, json, datetime
from pathlib import Path
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
SPDJI = "https://www.visualcapitalist.com/top-sp-500-stocks-by-annual-returns/"
def exa(t, y): return f"https://exa.ai/library/markets/stock/{t}?date={y}-12-31&view=return&from={y}-01-01&basis=total_return"

# year: (ticker, company, yahoo_symbol or None, status, sources, note)
W = {
 1996: (None, None, None, "unverified", [], "No reliable year-end 1996 list found. StatMuse (current-member screen) names Jabil, which was not an S&P 500 member in 1996; Yahoo has no data for most 1996 members (many delisted, tickers reused). Left blank on purpose."),
 1997: ("DELL", "Dell Computer", None, "single-source", [SPDJI], "S&P DJI top-50 list: +216% (price). Old Dell (taken private 2013) not on Yahoo; no second source found."),
 1998: ("DELL", "Dell Computer", None, "single-source", [SPDJI], "S&P DJI top-50 list: +248% (price; no dividends, so = total return)."),
 1999: ("QCOM", "Qualcomm", "QCOM", "verified", [SPDJI, "yahoo"], ""),
 2000: ("DYN", "Dynegy", None, "uncertain", [SPDJI, exa("DYN", 2000)], "S&P DJI lists Dynegy +361% (price) as 2000's top gainer, but Exa's total-return index shows +224%, which would put it below Reebok (+234%, also on the S&P DJI list). Dynegy merged with Illinova in Feb 2000, which likely explains the discrepancy. S&P DJI ranking kept; treat as uncertain."),
 2001: ("NVDA", "NVIDIA", "NVDA", "verified", [SPDJI, "yahoo"], "Joined the index Nov 2001 (year-end member)."),
 2002: ("PVN", "Providian Financial", None, "verified", ["https://www.iese.edu/media/research/pdfs/DI-0501-E.pdf", exa("PVN", 2002)], "Acquired by Washington Mutual Oct 2005 (after the holding window)."),
 2003: ("AV", "Avaya", None, "verified", [SPDJI, exa("AV", 2003)], "Taken private Oct 2007 (after the holding window)."),
 2004: ("ADSK", "Autodesk", "ADSK", "probable", ["yahoo", "https://www.statmuse.com/money/ask/best-performing-tech-stocks-in-the-s-and-p-500-in-the-year-2004"], "Yahoo coverage of 2004 members is incomplete (delisted names missing); no contemporaneous full-index list found."),
 2005: ("VLO", "Valero Energy", "VLO", "verified", ["yahoo", "https://money.cnn.com/2005/12/21/markets/sp_winners/"], ""),
 2006: ("ATI", "Allegheny Technologies", "ATI", "probable", ["yahoo", "https://www.statmuse.com/money/ask/20-best-performing-stocks-in-the-s-and-p-2006"], "StatMuse ranks ICE first, but ICE joined the S&P 500 only in 2007; ATI is next (+151%). Yahoo coverage of 2006 members incomplete."),
 2007: ("NOV", "National Oilwell Varco", "NOV", "verified", ["yahoo", "https://www.reuters.com/article/business/factbox-sp-500s-best-and-worst-performers-for-2007-idUSN31600240/"], ""),
 2008: ("FDO", "Family Dollar Stores", None, "verified", ["https://www.cnbc.com/2008/12/31/best-stock-of-2008.html", exa("FDO", 2008)], "Acquired by Dollar Tree Jul 2015 (after the holding window)."),
 2009: ("XL", "XL Capital", None, "verified", [SPDJI, exa("XL", 2009)], "Acquired by AXA 2018 (after the holding window)."),
 2010: ("NFLX", "Netflix", "NFLX", "verified", [SPDJI, "yahoo"], ""),
 2011: ("COG", "Cabot Oil & Gas", None, "verified", ["https://www.cnbc.com/2011/12/28/The-Best-Investments-of-2011.html", "https://www.thetimes-tribune.com/2012/01/07/cabot-tops-sp-500/", exa("COG", 2011)], "Now Coterra (CTRA); no longer on Yahoo, Exa total-return index used."),
 2012: ("PHM", "PulteGroup", "PHM", "verified", ["yahoo", "https://www.thefiscaltimes.com/2013/01/01/2012-Stock-Market-Winners-and-Losers"], ""),
 2013: ("NFLX", "Netflix", "NFLX", "verified", [SPDJI, "yahoo"], ""),
 2014: ("LUV", "Southwest Airlines", "LUV", "verified", ["yahoo", "press"], ""),
 2015: ("NFLX", "Netflix", "NFLX", "verified", ["yahoo", "press"], ""),
 2016: ("NVDA", "NVIDIA", "NVDA", "verified", [SPDJI, "yahoo"], ""),
 2017: ("NRG", "NRG Energy", "NRG", "verified", ["yahoo", "https://talkmarkets.com/content/stocks--equities/best-and-worst-performing-sp-500-stocks-in-2017?post=161048"], "Align Technology (+131%, joined Jun 2017) was a close second."),
 2018: ("AMD", "Advanced Micro Devices", "AMD", "verified", ["yahoo", "press"], ""),
 2019: ("AMD", "Advanced Micro Devices", "AMD", "verified", ["yahoo", "press"], ""),
 2020: ("TSLA", "Tesla", "TSLA", "verified", [SPDJI, "yahoo"], "Joined the index Dec 21, 2020 (year-end member)."),
 2021: ("DVN", "Devon Energy", "DVN", "verified", ["yahoo", "press"], ""),
 2022: ("OXY", "Occidental Petroleum", "OXY", "verified", ["yahoo", "press"], ""),
 2023: ("NVDA", "NVIDIA", "NVDA", "verified", ["yahoo", "press"], ""),
 2024: ("PLTR", "Palantir", "PLTR", "verified", ["yahoo", "https://www.fool.com/investing/2024/11/23/the-top-sp-500-stock-of-2024-so-far-isnt-nvidia-he/"], "Joined the index Sep 2024 (year-end member). Using Jan-1 members instead would give Vistra/NVIDIA."),
 2025: ("WDC", "Western Digital", "WDC", "verified", ["yahoo", "https://www.barrons.com/articles/western-digital-stock-best-sp500-2025-trade-desk-worst-f1bfdea5", "https://rubiewealth.com/sp-500-top-25-annual-performers-2025/"], "SanDisk (spun off Feb 2025, added Nov 2025) rose more from its debut but has no full-year 2025 return."),
 2026: ("MRNA", "Moderna", "MRNA", "ytd", ["yahoo", "https://topstocksytd.com/best-stocks-2026-so-far/"], "Year to date only. The +663% claim checks out against Yahoo closes (Dec 31, 2025 to the latest close)."),
}
# Hard-coded returns for names with no Yahoo history. price = winner-year price return; tr = total return by year.
HARD_PRICE = {1997: 2.16, 1998: 2.48, 2000: 3.61, 2002: 0.8282, 2003: 4.2816, 2008: 0.3863, 2009: 3.95, 2011: 1.0096}
HARD_PRICE_SRC = {2002: "Exa total return (no dividends paid)", 2003: "Exa total return (no dividends paid)", 2008: "Exa total return", 2011: "Exa total return"}
HARD_TR = {  # (ticker, year): total return, source
 ("DELL", 1998): (2.48, SPDJI), ("DELL", 1999): (0.3937, exa("DELL", 1999)), ("DELL", 2000): (-0.6581, exa("DELL", 2000)),
 ("DYN", 2001): (-0.5412, exa("DYN", 2001)), ("DYN", 2002): (-0.9530, exa("DYN", 2002)),
 ("PVN", 2003): (0.7935, exa("PVN", 2003)), ("PVN", 2004): (0.4149, exa("PVN", 2004)),
 ("AV", 2004): (0.3292, exa("AV", 2004)), ("AV", 2005): (-0.3797, exa("AV", 2005)),
 ("FDO", 2009): (0.0874, exa("FDO", 2009)), ("FDO", 2010): (0.8135, exa("FDO", 2010)),
 ("XL", 2010): (0.2155, exa("XL", 2010)), ("XL", 2011): (-0.0743, exa("XL", 2011)),
 ("COG", 2012): (0.3133, exa("COG", 2012)), ("COG", 2013): (0.5613, exa("COG", 2013)),
}

syms = sorted({v[2] for v in W.values() if v[2]} | {"^SP500TR"})
px = yf.download(syms, start="1995-12-01", end=datetime.date.today().isoformat(),  # completed sessions only
                 auto_adjust=False, progress=False)
close, adj = px["Close"], px["Adj Close"]
last_date = adj["^SP500TR"].dropna().index[-1]
LAST_YEAR = last_date.year

def ye(series, y):
    s = series[series.index <= f"{y}-12-31"].dropna()
    return s.iloc[-1] if len(s) else None

def ret(series, y):
    a, b = ye(series, y - 1), ye(series, y)
    return None if a is None or b is None else float(b / a - 1)

def tr(ticker, sym, y):
    """Total return of a winner in calendar year y -> (value, source, partial)."""
    if y > LAST_YEAR: return None, "", False
    if (ticker, y) in HARD_TR: v, s = HARD_TR[(ticker, y)]; return v, s, False
    if sym: return ret(adj[sym], y), "Yahoo Finance adj. close", y == LAST_YEAR
    return None, "", False

rows = []
for y in range(1996, LAST_YEAR + 1):
    t, name, sym, status, srcs, note = W[y]
    r = dict(year=y, ticker=t or "", company=name or "", status=status, note=note,
             sources=" | ".join(srcs))
    if t:
        if sym: r["return"] = ret(close[sym], y); r["return_src"] = "Yahoo Finance close (split-adj.)"
        else: r["return"] = HARD_PRICE[y]; r["return_src"] = HARD_PRICE_SRC.get(y, "S&P DJI via Visual Capitalist")
        n1, s1, p1 = tr(t, sym, y + 1); n2, s2, p2 = tr(t, sym, y + 2)
        r["next_year"] = n1; r["next_year_partial"] = p1
        r["two_year"] = (1 + n1) * (1 + n2) - 1 if n1 is not None and n2 is not None else (n1 if p1 else None)
        r["two_year_partial"] = p1 or p2
        r["fwd_src"] = "; ".join(sorted({s for s in (s1, s2) if s}))
    rows.append(r)
win = {r["year"]: r for r in rows}

# ---- Backtest (annual steps, total return). Starts Jan 1 1999: first year where both
# sleeves of Strategy B are defined (1996 winner unverified).
START = 1999
sp = {y: ret(adj["^SP500TR"], y) for y in range(START, LAST_YEAR + 1)}
def hold_ret(wy, y):
    w = win.get(wy)
    if not w or not w["ticker"]: return None
    return tr(w["ticker"], W[wy][2], y)[0]

A, B, S = [100.0], [100.0], [100.0]
sleeves = {0: 50.0, 1: 50.0}  # sleeve buys winner of year (y-1) when (y-1) % 2 == k ... held for 2 years
steps = []
for y in range(START, LAST_YEAR + 1):
    ra = hold_ret(y - 1, y)
    # Strategy B: sleeve k holds winner of year wy where wy ≡ k (mod 2) and wy in {y-1, y-2}
    rb_parts = {}
    for k in (0, 1):
        wy = y - 1 if (y - 1) % 2 == k else y - 2
        rb_parts[k] = (wy, hold_ret(wy, y))
    for k, (wy, rr) in rb_parts.items(): sleeves[k] *= 1 + rr
    A.append(A[-1] * (1 + ra)); S.append(S[-1] * (1 + sp[y])); B.append(sleeves[0] + sleeves[1])
    steps.append(dict(year=y, partial=(y == LAST_YEAR), sp500=sp[y], a_ticker=win[y - 1]["ticker"], a=ra,
                      b_holdings=[win[wy]["ticker"] for wy, _ in rb_parts.values()],
                      b=B[-1] / B[-2] - 1))

def stats(v):
    yrs = (last_date - datetime.datetime(START, 1, 1)).days / 365.25
    peak, mdd = v[0], 0
    for x in v: peak = max(peak, x); mdd = min(mdd, x / peak - 1)
    return dict(ending=v[-1], cagr=(v[-1] / v[0]) ** (1 / yrs) - 1, max_drawdown=mdd)

out = dict(generated=datetime.date.today().isoformat(), last_close=last_date.date().isoformat(), start=START,
           winners=rows, steps=steps,
           series=dict(labels=[f"{START}-01-01"] + [f"{y}-12-31" for y in range(START, LAST_YEAR)] + [last_date.date().isoformat()],
                       sp500=S, a=A, b=B),
           stats=dict(sp500=stats(S), a=stats(A), b=stats(B)))
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data/momentum.json").write_text(json.dumps(out, indent=1, default=float))
with open(ROOT / "data/momentum_winners.csv", "w", newline="") as f:
    cols = ["year", "ticker", "company", "return", "return_src", "next_year", "next_year_partial", "two_year",
            "two_year_partial", "fwd_src", "status", "sources", "note"]
    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
    for r in rows: w.writerow(r)
for k, v in out["stats"].items(): print(k, {a: round(b, 4) for a, b in v.items()})
