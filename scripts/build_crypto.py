#!/usr/bin/env python3
"""Build the Crypto Momentum dataset (data/crypto.json, data/crypto_winners.csv, data/crypto_top5.csv).

Source: CoinMarketCap historical snapshots (https://coinmarketcap.com/historical/YYYYMMDD/), read through the
same JSON endpoint the snapshot pages use. Each Dec 31 snapshot is taken at the end of the UTC day, so its price
is the Dec 31 UTC close. Snapshots are cached in .cache/cmc/ (not committed).

Rules
- Universe for year Y: top 100 coins by market cap in the Dec 31, Y snapshot, after removing stablecoins,
  wrapped/bridged tokens, liquid-staking receipts and gold/commodity-pegged tokens.
- Eligible: listed with a price in the Dec 31, Y-1 snapshot (a full year of price history).
- Thin-trading screen: coins whose Dec 31, Y 24h volume is under 0.1% of market cap are not used as winners
  (their caps are not backed by trading; e.g. CMC prices with zero volume). They are listed per year.
- Return = Dec 31 Y close / Dec 31 Y-1 close - 1 (USD).
- Strategy A: on Jan 1 buy last year's #1, hold 12 months. Strategy B: 20% in each of last year's top 5.
  A coin missing from a later snapshot is valued from CMC's daily history (last traded close if it stopped
  trading), so dead coins show their real outcome.
- Benchmark: Bitcoin buy-and-hold (same snapshots). S&P 500 total return (from data/momentum.json) for reference.
"""
import csv, json, os, re, sys, time, urllib.request, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "cmc"); os.makedirs(CACHE, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0"}
FIRST, LAST_FULL = 2015, 2025
THIN = 0.001
SNAP_URL = "https://coinmarketcap.com/historical/{}/"

def http(u):
    for i in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60))
        except Exception as e:
            if i == 3: raise
            time.sleep(3 * (i + 1))

def snapshot(date):
    f = os.path.join(CACHE, f"{date}.json")
    if not os.path.exists(f):
        out, start = [], 1
        while True:
            d = http(f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical?date={date}&limit=5000&start={start}&convertId=2781")["data"]
            out += d
            if len(d) < 5000: break
            start += 5000; time.sleep(1)
        json.dump(out, open(f, "w")); time.sleep(1)
    return {c["id"]: c for c in json.load(open(f))}

def q(c): return c["quotes"][0]

def daily(cid, date):
    """CMC daily close for coin `cid` on `date`, or the last close before it if the coin stopped trading."""
    f = os.path.join(CACHE, f"hist_{cid}_{date}.json")
    if not os.path.exists(f):
        d0 = dt.date.fromisoformat(date)
        ts = lambda d: int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())
        r = http(f"https://api.coinmarketcap.com/data-api/v3/cryptocurrency/historical?id={cid}&convertId=2781&timeStart={ts(d0 - dt.timedelta(days=365))}&timeEnd={ts(d0 + dt.timedelta(days=1))}")
        json.dump(r, open(f, "w")); time.sleep(1)
    r = json.load(open(f))["data"]
    pts = [(x["timeOpen"][:10], x["quote"]["close"]) for x in r.get("quotes", []) if x["timeOpen"][:10] <= date]
    return pts[-1] if pts else (None, None)

GOV = {"RSR", "FXS", "LDO", "RPL", "JTO", "LINK", "MKR", "SKY", "ENA", "CVX", "CRV", "HKG", "SPELL", "LQTY", "PENDLE"}
MANUAL = {"SAI", "DAI", "USNBT", "BITUSD", "BITCNY", "VBNB", "STETH", "WSTETH", "WBETH", "RETH", "JITOSOL", "BNSOL",
          "SLISBNB", "SLISBNBX", "WEETH", "RSETH", "CBBTC", "BTCB", "LBTC", "SOLVBTC", "FBTC", "BBTC", "HBTC", "WTRX",
          "AETHWETH", "AETHUSDT", "BTCT", "METH", "EZETH", "MSOL", "CBETH", "OSETH", "LSETH", "KHYPE", "JUPSOL", "RENBTC",
          "JLP", "WEOS", "WHBAR", "WZEDX", "WFLR", "WXTZ", "TBTC", "XSOLVBTC", "CETH", "CDAI", "CUSDC", "WHT", "WOKT"}
def excluded(c):
    s = c["symbol"].upper(); n = c["name"].lower(); t = set(c.get("tags") or [])
    if s in GOV: return None
    if s in MANUAL: return "pegged/derivative (manual list)"
    if t & {"stablecoin", "asset-backed-stablecoin", "algorithmic-stablecoin"}: return "stablecoin"
    if t & {"wrapped-tokens", "tokenized-gold", "tokenized-commodities", "tokenized-stock"}: return "wrapped/pegged"
    if re.search(r"wrapped|bridged|staked|tether|stable|\bgold\b|pegged|nubits|bitusd|bitcny", n) and "bitcoin gold" not in n: return "name indicates pegged/wrapped"
    if re.search(r"USD|EUR|CNY|KRW|JPY|GBP|XAU", s): return "fiat/gold ticker"
    return None

def year_table(y, end_date):
    cur, prev = snapshot(end_date), snapshot(f"{y-1}-12-31")
    ranked = sorted([c for c in cur.values() if (q(c).get("marketCap") or 0) > 0], key=lambda c: -q(c)["marketCap"])
    top = [c for c in ranked if not excluded(c)][:100]
    rows, thin, young = [], [], 0
    for rank, c in enumerate(top, 1):
        p = prev.get(c["id"])
        if not p or not q(p).get("price") or c["dateAdded"][:10] > f"{y-1}-12-31": young += 1; continue
        vr = (q(c).get("volume24h") or 0) / q(c)["marketCap"]
        r = dict(id=c["id"], symbol=c["symbol"], name=c["name"], slug=c["slug"], rank=rank, p0=q(p)["price"], p1=q(c)["price"],
                 ret=q(c)["price"] / q(p)["price"] - 1, mcap=q(c)["marketCap"], vol=q(c).get("volume24h") or 0, vol_ratio=vr)
        (thin if vr < THIN else rows).append(r)
    rows.sort(key=lambda r: -r["ret"]); thin.sort(key=lambda r: -r["ret"])
    cut = rows[4]["ret"] if len(rows) >= 5 else -1
    return dict(universe=len(top), eligible=len(rows), too_new=young, rows=rows, thin=[t for t in thin if t["ret"] > cut])

def hold_price(c, date):
    snap = snapshot(date)
    if c["id"] in snap and q(snap[c["id"]]).get("price"):
        return q(snap[c["id"]])["price"], "snapshot", date
    d, px = daily(c["id"], date)
    if px: return px, ("cmc daily history" + ("" if d == date else f", last close {d}")), d
    # No daily history: walk back through weekly snapshots to the last one that still lists the coin.
    d = dt.date.fromisoformat(date); d -= dt.timedelta(days=(d.weekday() + 1) % 7)  # previous Sunday
    stop = d - dt.timedelta(days=366)
    while d > stop:
        sn = snapshot(d.isoformat())
        if c["id"] in sn and q(sn[c["id"]]).get("price"):
            return q(sn[c["id"]])["price"], f"dropped from CMC listings; valued at last listed price ({d.isoformat()} snapshot)", d.isoformat()
        d -= dt.timedelta(days=7)
    return None, "no price found", None

def main():
    today = dt.date.today()
    last = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
    years = list(range(FIRST, LAST_FULL + 1))
    T = {y: year_table(y, f"{y}-12-31") for y in years}
    ytd = year_table(LAST_FULL + 1, last)
    # flags (written by hand after review; see notes in the CSV)
    CL = "https://www.coinlore.com/top-crypto-coins/{}"
    # status: verified = CMC figure matches an independent source; single-source = CMC only; flag = caveat below.
    NOTES = {
        2015: ("flag", "Very thin market: outside BTC, XRP, LTC and ETH the top 100 traded little (GAME's Dec 31 volume was about $6k). Higher raw returns (RBY +6,782%, EMC, AC) fail the volume screen. CMC only.", ""),
        2016: ("verified", "Monero is also #1 on CoinLore's 2016 list ($0.50 to $13.36).", CL.format(2016)),
        2017: ("verified", "Verge's ~11,500x gain matches CoinLore's 2017 #1 ($0.000023 to $0.186). Most of 2017's top 100 were new ICOs without a Dec 2016 price.", CL.format(2017)),
        2018: ("single-source", "No coin in the year-end top 100 with a full year of history rose in 2018; the best was BNB (-29%). Single Collateral DAI (+1%) is a stablecoin and excluded.", ""),
        2019: ("flag", "MOF and SEELE were small exchange-driven tokens. CMC's snapshot and daily data agree, but no independent source was found.", ""),
        2020: ("flag", "HEX was added to CMC on Dec 17, 2019, so its start price comes from two weeks of trading. HSN (+3.7 million%) and CRD (+86,722%) had zero reported volume and are excluded by the volume screen.", ""),
        2021: ("verified", "CoinLore also has GALA as the top full-year gainer among established coins (+38,514% from its Jan 1 price; CMC Dec 31 to Dec 31: +31,217%). CoinDesk/Messari's $5B+ list has SAND #1 (+16,265%).", CL.format(2021)),
        2022: ("verified", "Trust Wallet Token matches CoinLore ($0.72 to $1.40).", CL.format(2022)),
        2023: ("flag", "BONK launched Dec 25, 2022, so its start price comes from one week of trading. Bitcoin Classic (BXC, +37 million%) is excluded by the volume screen ($0.7M volume on a $2.6B cap).", ""),
        2024: ("verified", "CryptoPotato/CryptoRank also rank MANTRA as the best top-100 coin with a full year of trading (+8,776% on their dates; Fartcoin launched mid-year).", "https://cryptopotato.com/the-top-100-crypto-assets-6-best-performers-in-2024/"),
        2025: ("single-source", "AB is the rebranded Newton (NEW) project, renamed 1:1 on Feb 18, 2025 (same CMC id 3871), so the return is not distorted by a swap.", "https://www.chaincatcher.com/en/article/2168208"),
    }
    winners, top5 = [], []
    for y in years:
        t = T[y]; w = t["rows"][0]
        winners.append(dict(year=y, symbol=w["symbol"], name=w["name"], cmc_id=w["id"], ret=w["ret"], p0=w["p0"], p1=w["p1"],
                            mcap_rank=w["rank"], universe=t["universe"], eligible=t["eligible"],
                            thin_excluded="; ".join(f'{x["symbol"]} {x["ret"]*100:+,.0f}%' for x in t["thin"]),
                            status=NOTES[y][0], note=NOTES[y][1],
                            source=" ; ".join(x for x in (SNAP_URL.format(f"{y}1231"), SNAP_URL.format(f"{y-1}1231"), NOTES[y][2]) if x)))
        top5.append(dict(year=y, names=[dict(symbol=r["symbol"], name=r["name"], id=r["id"], ret=r["ret"], p0=r["p0"], p1=r["p1"]) for r in t["rows"][:5]]))
    yw = ytd["rows"][0]
    ytd_out = dict(year=LAST_FULL + 1, as_of=last, symbol=yw["symbol"], name=yw["name"], ret=yw["ret"], vol=yw["vol"], mcap=yw["mcap"],
                   top=[dict(symbol=r["symbol"], name=r["name"], ret=r["ret"]) for r in ytd["rows"][:5]],
                   thin_excluded=[f'{x["symbol"]} {x["ret"]*100:+,.0f}%' for x in ytd["thin"]],
                   note="Single source (CMC), not verified. Small, thinly covered token; YTD to the last full UTC day.")
    # Next-year outcomes (holding period y+1)
    steps = []
    for y in years:
        hy = y + 1; end = f"{hy}-12-31" if hy <= LAST_FULL else last; partial = hy > LAST_FULL
        hold = []
        for r in T[y]["rows"][:5]:
            p1, how, d = hold_price(r, end)
            rr = p1 / r["p1"] - 1 if p1 else None
            hold.append(dict(symbol=r["symbol"], id=r["id"], buy=r["p1"], sell=p1, ret=rr, valued=how))
        if any(h["ret"] is None for h in hold): sys.exit(f"missing outcome {y}: {hold}")
        btc0, btc1 = q(snapshot(f"{y}-12-31")[1])["price"], q(snapshot(end)[1])["price"]
        steps.append(dict(year=hy, partial=partial, end=end, a=hold[0]["ret"], a_symbol=hold[0]["symbol"],
                          b=sum(h["ret"] for h in hold) / 5, holdings=hold, btc=btc1 / btc0 - 1))
    # S&P 500 TR reference from the momentum dataset (year-end values; last point = its latest close)
    m = json.load(open(os.path.join(ROOT, "data", "momentum.json")))
    spv = dict(zip(m["series"]["labels"], m["series"]["sp500"]))
    for s in steps:
        a, b = spv.get(f"{s['year']-1}-12-31"), (spv.get(s["end"]) if not s["partial"] else m["series"]["sp500"][-1])
        s["sp500"] = b / a - 1 if a and b else None
    start = FIRST + 1
    labels = [f"{start}-01-01"] + [s["end"] for s in steps]
    def path(k):
        v = [100.0]
        for s in steps: v.append(v[-1] * (1 + s[k]))
        return v
    series = dict(labels=labels, btc=path("btc"), a=path("a"), b=path("b"), sp500=path("sp500"))
    def stats(v):
        yrs = (dt.date.fromisoformat(last) - dt.date(start, 1, 1)).days / 365.25
        pk, mdd = v[0], 0
        for x in v: pk = max(pk, x); mdd = min(mdd, x / pk - 1)
        return dict(ending=round(v[-1], 2), cagr=round((v[-1] / v[0]) ** (1 / yrs) - 1, 4), max_drawdown=round(mdd, 4))
    out = dict(generated=today.isoformat(), last_close=last, start=start, first_year=FIRST,
               rules=dict(universe="Top 100 by market cap at each Dec 31 UTC snapshot (CoinMarketCap), excluding stablecoins, wrapped/bridged, liquid-staking and gold-pegged tokens",
                          eligibility="Priced in the prior Dec 31 snapshot", thin_screen=f"24h volume < {THIN:.1%} of market cap at year-end: not used"),
               sources=[dict(name="CoinMarketCap historical snapshots", url="https://coinmarketcap.com/historical/"),
                        dict(name="CoinMarketCap daily history (coins missing from later snapshots)", url="https://coinmarketcap.com/currencies/<slug>/historical-data/"),
                        dict(name="S&P 500 total return (^SP500TR via data/momentum.json)", url="https://finance.yahoo.com/quote/%5ESP500TR/")],
               winners=winners, top5=top5, ytd=ytd_out, steps=steps, series=series,
               stats={k: stats(series[k]) for k in ("btc", "a", "b", "sp500")})
    json.dump(out, open(os.path.join(ROOT, "data", "crypto.json"), "w"), indent=1)
    with open(os.path.join(ROOT, "data", "crypto_winners.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(winners[0].keys()) + ["next_year_ret", "next_year_valued"]); w.writeheader()
        for r, s in zip(winners, steps): w.writerow({**r, "next_year_ret": s["a"], "next_year_valued": s["holdings"][0]["valued"]})
        w.writerow(dict(year=f"{LAST_FULL+1} YTD to {last}", symbol=yw["symbol"], name=yw["name"], cmc_id=yw["id"], ret=yw["ret"], p0=yw["p0"], p1=yw["p1"],
                        mcap_rank=yw["rank"], thin_excluded="; ".join(ytd_out["thin_excluded"]), status="unverified", note="Year to date, single source (CMC); not verified",
                        source=SNAP_URL.format(f"{LAST_FULL}1231")))
    with open(os.path.join(ROOT, "data", "crypto_top5.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["year", "rank", "symbol", "name", "cmc_id", "return", "close_prev_dec31", "close_dec31", "next_year_ret", "next_year_valued"])
        for t, s in zip(top5, steps):
            for i, (n, h) in enumerate(zip(t["names"], s["holdings"]), 1):
                w.writerow([t["year"], i, n["symbol"], n["name"], n["id"], n["ret"], n["p0"], n["p1"], h["ret"], h["valued"]])
    for k, v in out["stats"].items(): print(k, v)

if __name__ == "__main__":
    main()
