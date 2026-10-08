#!/usr/bin/env python3
"""Refresh the inline MACRO_DATA block in index.html (the six indicator charts).

Sources (none send CORS headers, so the data is embedded like BAA_MONTHLY):
  FRED (public-domain U.S. government series):
    CPIAUCSL  CPI-U, seasonally adjusted, monthly   -> year-over-year % change
    GDP       Nominal GDP, quarterly SAAR           -> year-over-year % change
    GDPC1     Real GDP (chained dollars), quarterly -> year-over-year % change
    GS10      10-year Treasury constant maturity, monthly average, %
    DGS10     10-year Treasury constant maturity, daily, % (latest value only)
  Robert J. Shiller, "Irrational Exuberance" data (ie_data.xls from shillerdata.com):
    D / P * 100  S&P 500 dividend yield (trailing 4-quarter dividends / monthly avg price)
    E / P * 100  S&P 500 earnings yield (trailing 4-quarter reported earnings / monthly avg price)
    D_t / D_t-12 S&P 500 dividend growth: year-over-year % change in trailing 4-quarter dividends
                 (Shiller's monthly D is the S&P four-quarter dividend total, linearly interpolated
                 to months, so this is growth in trailing 12-month dividends per index share)
    CAPE         Shiller P/E (cyclically adjusted P/E, P/E10), monthly. Only a fallback for the
                 Shiller PE card, which normally loads History of Market's live CAPE series.

Requires: curl, and the `xlrd` package to read Shiller's .xls (pip install xlrd). If xlrd is
missing, it re-runs itself under /workspace/.pwvenv/bin/python when that exists (shared box).
Prints CHANGED or UNCHANGED. Leaves everything outside the marked block untouched.
"""
import csv, io, json, os, re, subprocess, sys
from pathlib import Path

START = "1996-01"  # same start as the main forward P/E chart
p = Path(__file__).with_name("index.html")


def curl(url, binary=False):
    r = subprocess.run(["curl", "-sfL", "--retry", "4", "--retry-all-errors", "--retry-delay", "3", "--max-time", "90", url],
                       capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def fred(series_id):
    raw = curl("https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + series_id)
    rows = list(csv.reader(io.StringIO(raw)))[1:]
    out = [(d, float(v)) for d, v in rows if v not in (".", "")]
    if len(out) < 100:
        sys.exit(f"ERROR: only {len(out)} rows from FRED {series_id}")
    return out


def yoy(levels):
    """Percent change vs. the same month/quarter one year earlier (matched by date, so
    missing observations, e.g. the unpublished Oct-2025 CPI, just leave a gap)."""
    by_date = dict(levels)
    out = []
    for d, v in levels:
        prev = by_date.get(f"{int(d[:4]) - 1:04d}{d[4:]}")
        if prev:
            out.append([d[:7], round((v / prev - 1) * 100, 2)])
    return [r for r in out if r[0] >= START]


def shiller():
    try:
        import xlrd
    except ImportError:
        # On the shared box, plain python3 lacks xlrd; re-run under the venv that has it.
        venv = Path("/workspace/.pwvenv/bin/python")
        if venv.exists() and not os.environ.get("UPDATE_MACRO_REEXEC"):
            os.environ["UPDATE_MACRO_REEXEC"] = "1"
            os.execv(str(venv), [str(venv), str(Path(__file__).resolve())] + sys.argv[1:])
        sys.exit("ERROR: the xlrd package is required (pip install xlrd)")
    page = curl("https://shillerdata.com/")
    m = re.search(r'(?:https?:)?//[^"\'\s<>]+/ie_data\.xls[^"\'\s<>]*', page)
    if not m:
        sys.exit("ERROR: ie_data.xls link not found on shillerdata.com")
    url = m.group(0)
    if url.startswith("//"):
        url = "https:" + url
    book = xlrd.open_workbook(file_contents=curl(url, binary=True))
    sh = book.sheet_by_name("Data")
    hdr = [str(x).strip() for x in sh.row_values(7)]
    if hdr[:4] != ["Date", "P", "D", "E"] or hdr[12] != "CAPE":
        sys.exit(f"ERROR: unexpected Shiller header {hdr[:4]} / {hdr[12:13]}")
    dy, ey, divs, cape = [], [], {}, []
    for r in range(8, sh.nrows):
        row = sh.row_values(r)
        date, P, D, E = row[:4]
        CAPE = row[12] if len(row) > 12 else None
        if not isinstance(date, float) or not isinstance(P, float) or P <= 0:
            continue
        y = int(date)
        mth = int(round((date - y) * 100))
        ym = f"{y}-{mth:02d}"
        if isinstance(D, float) and D > 0:
            divs[ym] = D
        if ym < START:
            continue
        if isinstance(D, float):
            dy.append([ym, round(D / P * 100, 2)])
        if isinstance(E, float):
            ey.append([ym, round(E / P * 100, 2)])
        if isinstance(CAPE, float) and CAPE > 0:
            cape.append([ym, round(CAPE, 2)])
    dg = []
    for ym in sorted(divs):
        prev = divs.get(f"{int(ym[:4]) - 1:04d}{ym[4:]}")
        if ym >= START and prev:
            dg.append([ym, round((divs[ym] / prev - 1) * 100, 2)])
    if len(dy) < 300 or len(ey) < 300 or len(dg) < 300 or len(cape) < 300:
        sys.exit(f"ERROR: too few Shiller rows (div {len(dy)}, earn {len(ey)}, growth {len(dg)}, cape {len(cape)})")
    return dy, ey, dg, cape


def main():
    div_yield, earn_yield, div_growth, cape = shiller()
    cpi = fred("CPIAUCSL")
    gdp = fred("GDP")
    rgdp = fred("GDPC1")
    gs10 = [[d[:7], round(v, 2)] for d, v in fred("GS10") if d[:7] >= START]
    dgs10 = fred("DGS10")
    data = {
        "div_yield": div_yield,
        "earn_yield": earn_yield,
        "div_growth": div_growth,
        "cape": cape,
        "cpi_yoy": yoy(cpi),
        "gdp_yoy": yoy(gdp),
        "rgdp_yoy": yoy(rgdp),
        "gs10": gs10,
        "dgs10_latest": [dgs10[-1][0], round(dgs10[-1][1], 2)],
    }
    for k, v in data.items():
        if k != "dgs10_latest" and len(v) < 100:
            sys.exit(f"ERROR: {k} has only {len(v)} points")
    html = p.read_text()
    new = "/*MACRO_DATA_START*/" + json.dumps(data, separators=(",", ":")) + "/*MACRO_DATA_END*/"
    out, n = re.subn(r"/\*MACRO_DATA_START\*/.*?/\*MACRO_DATA_END\*/", lambda m: new, html, count=1, flags=re.S)
    if n != 1:
        sys.exit("ERROR: MACRO_DATA block not found")
    latest = {k: (v[-1] if k != "dgs10_latest" else v) for k, v in data.items()}
    if out == html:
        print("UNCHANGED latest", latest)
        return
    p.write_text(out)
    print("CHANGED latest", latest)


if __name__ == "__main__":
    main()
