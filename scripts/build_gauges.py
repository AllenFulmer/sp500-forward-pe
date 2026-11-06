#!/usr/bin/env python3
"""Build data/gauges.json for the Gauges tab.

Fetches public series, computes each of the 14 models' current value and
z-score (capped at +/-2), writes sparkline history, composite signal, and
NY Fed recession probability.

Usage:
  python3 scripts/build_gauges.py           # full rebuild
  python3 scripts/build_gauges.py --daily   # lightweight: refresh daily series only

On the shared box, prefer:
  /workspace/.pwvenv/bin/python scripts/build_gauges.py
(system python3 also works if xlrd is importable from that venv's site-packages).

Licence policy (see comments on each model):
  - Public-domain / citation-requested FRED & Fed: embed with credit
  - Copyrighted: Citation Required (VIX, UMCSENT, BAA, T10Y2Y, OECD): embed with
    attribution per FRED legal (display OK with citation)
  - Copyrighted: Pre-Approval Required (ICE BofA HY OAS): DO NOT embed; use
    Moody's Baa minus 10Y Treasury as a labeled substitute
  - History of Market CAPE/forward PE: CC BY 4.0, credit required
  - Price/Sales: no free redistributable source -> status unavailable
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "gauges.json"
CACHE = Path(os.environ.get("GAUGES_CACHE", "/tmp/gauges-cache"))
CACHE.mkdir(parents=True, exist_ok=True)

# Make xlrd importable when running under system python3 on the shared box.
_VENV_SP = Path("/workspace/.pwvenv/lib/python3.13/site-packages")
if _VENV_SP.exists() and str(_VENV_SP) not in sys.path:
    sys.path.insert(0, str(_VENV_SP))

try:
    import xlrd
except ImportError:
    venv = Path("/workspace/.pwvenv/bin/python")
    if venv.exists() and not os.environ.get("BUILD_GAUGES_REEXEC"):
        os.environ["BUILD_GAUGES_REEXEC"] = "1"
        os.execv(str(venv), [str(venv), str(Path(__file__).resolve())] + sys.argv[1:])
    sys.exit("ERROR: xlrd is required (pip install xlrd)")

try:
    import openpyxl
except ImportError:
    openpyxl = None  # FINRA path falls back to HTML table scrape


# ---------------------------------------------------------------------------
# HTTP / CSV helpers
# ---------------------------------------------------------------------------

def curl(url: str, binary: bool = False, timeout: int = 120) -> bytes | str:
    r = subprocess.run(
        ["curl", "-sfL", "--retry", "4", "--retry-all-errors", "--retry-delay", "2",
         "--max-time", str(timeout), url],
        capture_output=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"curl failed ({r.returncode}) for {url}: {r.stderr[:200]!r}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def fred_csv(series_id: str) -> list[tuple[str, float]]:
    """Download a FRED series via fredgraph CSV (no API key). Returns (YYYY-MM-DD, value)."""
    raw = curl(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}")
    rows = list(csv.reader(io.StringIO(raw)))
    if not rows or "observation_date" not in rows[0][0].lower() and rows[0][0] != "observation_date":
        # header is observation_date,SERIES
        pass
    out = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        d, v = row[0].strip(), row[1].strip()
        if not d or v in (".", ""):
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    if len(out) < 20:
        raise RuntimeError(f"FRED {series_id}: only {len(out)} rows")
    return out


def monthlyize_last(daily: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Keep last observation of each YYYY-MM."""
    by = {}
    for d, v in daily:
        by[d[:7]] = (d, v)
    return [(d, v) for _, (d, v) in sorted(by.items())]


def yoy_pct(levels: list[tuple[str, float]]) -> list[tuple[str, float]]:
    by = {d[:7]: v for d, v in levels}
    out = []
    for d, v in levels:
        key = d[:7]
        prev_key = f"{int(key[:4]) - 1:04d}{key[4:]}"
        prev = by.get(prev_key)
        if prev and prev != 0:
            out.append((d, (v / prev - 1.0) * 100.0))
    return out


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def mean_std(vals: list[float]) -> tuple[float, float]:
    if len(vals) < 3:
        return (vals[-1] if vals else 0.0), 1.0
    m = statistics.fmean(vals)
    # population-ish sample std; use at least a tiny floor
    s = statistics.stdev(vals) if len(vals) > 1 else 1.0
    if s < 1e-9:
        s = 1.0
    return m, s


def zscore_series(pairs: list[tuple[str, float]], window_start: str | None = "1990-01") -> list[tuple[str, float, float]]:
    """Return (date, value, z) using expanding mean/std of values with date >= window_start.
    Z for each point uses the full window history up to 'now' (same mean/std for all points
    based on the full sample in-window) — matches a stable gauge reading.
    """
    if window_start:
        sample = [(d, v) for d, v in pairs if d[:7] >= window_start]
    else:
        sample = list(pairs)
    if len(sample) < 5:
        sample = list(pairs)
    vals = [v for _, v in sample]
    m, s = mean_std(vals)
    return [(d, v, (v - m) / s) for d, v in sample]


def clamp_sd(sd: float) -> float:
    return max(-2.0, min(2.0, sd))


def linreg(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs) or 1.0
    b = num / den
    a = my - b * mx
    return a, b


# ---------------------------------------------------------------------------
# Source loaders
# ---------------------------------------------------------------------------

def load_shiller() -> dict:
    """Return dict with monthly lists: date(YYYY-MM), P, E, CAPE, GS10_shiller."""
    page = curl("https://shillerdata.com/")
    m = re.search(r'(?:https?:)?//[^"\'\s<>]+/ie_data\.xls[^"\'\s<>]*', page)
    if not m:
        raise RuntimeError("ie_data.xls link not found on shillerdata.com")
    url = m.group(0)
    if url.startswith("//"):
        url = "https:" + url
    book = xlrd.open_workbook(file_contents=curl(url, binary=True))
    sh = book.sheet_by_name("Data")
    hdr = [str(x).strip() for x in sh.row_values(7)]
    # Date, P, D, E, ... CAPE at index 12
    out = {"date": [], "P": [], "E": [], "CAPE": [], "D": []}
    for r in range(8, sh.nrows):
        row = sh.row_values(r)
        if not row or row[0] in ("", None):
            continue
        # Date as YYYY.MM float
        try:
            df = float(row[0])
        except (TypeError, ValueError):
            continue
        year = int(df)
        month = int(round((df - year) * 100))
        if month < 1 or month > 12:
            continue
        ds = f"{year:04d}-{month:02d}"
        P, E, D = row[1], row[3], row[2]
        CAPE = row[12] if len(row) > 12 else None
        def ok(x):
            return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x != 0
        if not ok(P):
            continue
        out["date"].append(ds)
        out["P"].append(float(P))
        out["E"].append(float(E) if ok(E) else None)
        out["D"].append(float(D) if ok(D) else None)
        out["CAPE"].append(float(CAPE) if ok(CAPE) else None)
    if len(out["date"]) < 100:
        raise RuntimeError("Shiller parse too short")
    return out


def load_hom_cape() -> list[tuple[str, float]]:
    data = json.loads(curl("https://historyofmarket.com/api/sp500/pe.json"))
    series = data.get("cape") or []
    out = []
    for p in series:
        d, v = p.get("date"), p.get("value")
        if d and isinstance(v, (int, float)):
            out.append((d[:10], float(v)))
    return out


def load_hom_forward_pe() -> list[tuple[str, float]]:
    data = json.loads(curl("https://historyofmarket.com/api/sp500/forward-pe.json"))
    series = data.get("forward") or []
    out = []
    for p in series:
        d, v = p.get("date"), p.get("value")
        if d and isinstance(v, (int, float)) and v > 0:
            out.append((d[:10], float(v)))
    return out


def load_finra_margin() -> list[tuple[str, float]]:
    """Debit balances in $ millions, monthly. Prefer Excel; fall back to HTML table."""
    url = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
    try:
        raw = curl(url, binary=True)
        if openpyxl is None:
            raise RuntimeError("openpyxl missing")
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        out = []
        for row in rows[1:]:
            if not row or row[0] is None or row[1] is None:
                continue
            ym = str(row[0]).strip()  # YYYY-MM
            if re.match(r"^\d{4}-\d{2}$", ym):
                out.append((ym + "-01", float(row[1])))
        out.sort()
        if len(out) > 50:
            return out
    except Exception as e:
        print(f"[warn] FINRA xlsx failed ({e}); trying HTML", file=sys.stderr)

    html = curl("https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics")
    # rows like <td>Sep-26</td><td>1,480,054</td>
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    out = []
    for mon, yy, num in re.findall(
        r"<td>(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{2})</td>\s*<td>([0-9,]+)</td>",
        html,
    ):
        year = 2000 + int(yy)
        if year > datetime.now().year + 1:
            year -= 100
        val = float(num.replace(",", ""))
        out.append((f"{year:04d}-{months[mon]:02d}-01", val))
    out.sort()
    if len(out) < 6:
        raise RuntimeError("FINRA margin parse failed")
    return out


def load_philly_state_decline() -> list[tuple[str, float]]:
    """Share of 50 states with a 3-month decline in the coincident index, in percent."""
    raw = curl(
        "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/coincident/coincident-revised.xls",
        binary=True,
    )
    book = xlrd.open_workbook(file_contents=raw)
    sh = book.sheet_by_name("Indexes")
    hdr = [sh.cell_value(0, c) for c in range(sh.ncols)]
    state_cols = [i for i, h in enumerate(hdr) if i > 0 and str(h).upper() != "US"]
    # Collect rows with date + values
    rows = []
    for r in range(1, sh.nrows):
        dv = sh.cell_value(r, 0)
        if dv in ("", None):
            continue
        try:
            dt = xlrd.xldate_as_datetime(dv, book.datemode)
        except Exception:
            continue
        vals = []
        for c in state_cols:
            v = sh.cell_value(r, c)
            vals.append(float(v) if isinstance(v, (int, float)) and v == v else None)
        rows.append((dt.strftime("%Y-%m-01"), vals))
    out = []
    for i in range(3, len(rows)):
        d, cur = rows[i]
        _, prev = rows[i - 3]
        decl = 0
        n = 0
        for a, b in zip(cur, prev):
            if a is None or b is None:
                continue
            n += 1
            if a < b:
                decl += 1
        if n >= 40:
            out.append((d, 100.0 * decl / n))
    if len(out) < 20:
        raise RuntimeError("Philly state decline series too short")
    return out


def load_nyfed_recession_prob() -> tuple[str, float, list[tuple[str, float]]]:
    """Latest NY Fed 12-month-ahead recession probability + history.
    Returns (as_of_date, prob_pct_0to100, history list of (date, pct)).
    """
    raw = curl(
        "https://www.newyorkfed.org/medialibrary/media/research/capital_markets/allmonth.xls",
        binary=True,
    )
    book = xlrd.open_workbook(file_contents=raw)
    sh = book.sheet_by_index(0)
    hist = []
    for r in range(1, sh.nrows):
        dcell, rp = sh.cell_value(r, 0), sh.cell_value(r, 5)
        spread = sh.cell_value(r, 4)
        # Prefer rows that still have the spread (observation months), not dangling forecast-only rows
        if rp in ("", None) or not isinstance(rp, (int, float)):
            continue
        if spread in ("", None):
            continue
        try:
            dt = xlrd.xldate_as_datetime(dcell, book.datemode)
        except Exception:
            continue
        hist.append((dt.strftime("%Y-%m-%d"), float(rp) * 100.0))
    if not hist:
        raise RuntimeError("NY Fed Rec_prob empty")
    return hist[-1][0], hist[-1][1], hist


# ---------------------------------------------------------------------------
# Model builders
# ---------------------------------------------------------------------------

SPARK_YEARS = 10  # sparkline length


def spark(pairs: list[tuple[str, float]], years: int = SPARK_YEARS) -> list[dict]:
    if not pairs:
        return []
    last = pairs[-1][0]
    # cutoff ~ years before last observation
    try:
        y = int(last[:4]) - years
        cut = f"{y:04d}{last[4:7]}"
    except Exception:
        cut = "0"
    out = []
    for d, v in pairs:
        if d[:7] >= cut[:7]:
            out.append({"date": d[:7], "value": round(v, 4) if abs(v) < 1000 else round(v, 2)})
    # de-dupe by month keeping last
    by = {}
    for p in out:
        by[p["date"]] = p
    return [by[k] for k in sorted(by)]


def unavailable(id_, name, category, desc, reason, source_name="", source_url=""):
    return {
        "id": id_,
        "name": name,
        "category": category,
        "desc": desc,
        "value": None,
        "value_display": "—",
        "unit": "",
        "sd": None,
        "direction": 1,
        "direction_note": "",
        "as_of": None,
        "status": "unavailable",
        "unavailable_reason": reason,
        "substitute_of": None,
        "substitute_note": None,
        "source_name": source_name,
        "source_url": source_url,
        "history": [],
        "window": None,
    }


def make_model(
    *,
    id_, name, category, desc,
    pairs: list[tuple[str, float]],
    direction: int,
    direction_note: str,
    unit: str,
    value_fmt: str,  # e.g. "{:.1f}%"
    source_name: str,
    source_url: str,
    window_start: str = "1990-01",
    status: str = "ok",
    substitute_of: str | None = None,
    substitute_note: str | None = None,
    display_pairs: list[tuple[str, float]] | None = None,
    display_fmt: str | None = None,
):
    """pairs = series used for z-score; display_pairs optional different series for the big number."""
    z = zscore_series(pairs, window_start)
    if not z:
        return unavailable(id_, name, category, desc, "series empty", source_name, source_url)
    d, v, raw_z = z[-1]
    sd = clamp_sd(direction * raw_z)
    disp_src = display_pairs if display_pairs is not None else pairs
    # align display value to same month if possible
    disp_v = v
    disp_d = d
    if display_pairs is not None:
        by = {x[:7]: (x, y) for x, y in display_pairs}
        if d[:7] in by:
            disp_d, disp_v = by[d[:7]]
        else:
            disp_d, disp_v = display_pairs[-1]
    fmt = display_fmt or value_fmt
    try:
        value_display = fmt.format(disp_v)
    except Exception:
        value_display = str(disp_v)
    return {
        "id": id_,
        "name": name,
        "category": category,
        "desc": desc,
        "value": round(disp_v, 4) if isinstance(disp_v, float) else disp_v,
        "value_display": value_display,
        "unit": unit,
        "sd": round(sd, 2),
        "sd_raw": round(raw_z, 3),
        "direction": direction,
        "direction_note": direction_note,
        "as_of": disp_d[:10],
        "status": status,
        "unavailable_reason": None,
        "substitute_of": substitute_of,
        "substitute_note": substitute_note,
        "source_name": source_name,
        "source_url": source_url,
        "history": spark(disp_src if display_pairs is not None else pairs),
        "window": f"z-score vs history since {window_start}",
    }


def build_all(daily_only: bool = False) -> dict:
    """Build full gauges payload. If daily_only and an existing file exists, reuse monthly models."""
    existing = None
    if daily_only and OUT.exists():
        try:
            existing = json.loads(OUT.read_text())
        except Exception:
            existing = None

    errors: list[str] = []
    models: dict[str, dict] = {}

    def keep_or(id_, builder):
        try:
            models[id_] = builder()
        except Exception as e:
            errors.append(f"{id_}: {e}")
            print(f"[error] {id_}: {e}", file=sys.stderr)
            if existing:
                old = next((m for m in existing.get("models", []) if m["id"] == id_), None)
                if old:
                    models[id_] = old
                    return
            # stub unavailable
            models[id_] = unavailable(id_, id_, "valuation", "", str(e))

    # ----- shared fetches -----
    print("Fetching FRED + market data…")
    gs10 = fred_csv("GS10")
    dgs10 = fred_csv("DGS10")
    baa = fred_csv("BAA")
    t10y2y = fred_csv("T10Y2Y")
    vix = fred_csv("VIXCLS")
    sahm = fred_csv("SAHMREALTIME")
    epu = fred_csv("USEPUINDXM")
    umcsent = fred_csv("UMCSENT")
    gdp = fred_csv("GDP")
    ncbe = fred_csv("NCBEILQ027S")  # corporate equities, millions

    shiller = None
    try:
        shiller = load_shiller()
    except Exception as e:
        errors.append(f"shiller: {e}")
        print(f"[error] shiller: {e}", file=sys.stderr)

    # ========== VALUATION ==========
    # Buffett Indicator
    def build_buffett():
        gdp_by = {d[:7]: v for d, v in gdp}
        pairs = []
        for d, eq in ncbe:
            g = gdp_by.get(d[:7])
            if g and g > 0:
                # eq is Millions of $, GDP is Billions of $ → eq/1000 / GDP
                pairs.append((d, (eq / 1000.0) / g * 100.0))
        return make_model(
            id_="buffett", name="Buffett Indicator", category="valuation",
            desc="Total Market Cap / GDP.",
            pairs=pairs, direction=1,
            direction_note="High = expensive (+)",
            unit="%", value_fmt="{:.0f}%",
            source_name="Fed Z.1 nonfinancial corporate equities (NCBEILQ027S) / BEA GDP via FRED",
            source_url="https://fred.stlouisfed.org/series/NCBEILQ027S",
            window_start="1990-01",
        )
    keep_or("buffett", build_buffett)

    # CAPE
    def build_cape():
        try:
            pairs = load_hom_cape()
            src = "History of Market (historyofmarket.com) CAPE, CC BY 4.0; methodology Robert Shiller"
            url = "https://historyofmarket.com/api/sp500/pe.json"
        except Exception:
            if not shiller:
                raise
            pairs = [(d + "-01", c) for d, c in zip(shiller["date"], shiller["CAPE"]) if c]
            src = "Robert Shiller CAPE via shillerdata.com"
            url = "https://shillerdata.com/"
        # monthlyize weekly HOM points for z-score stability
        pairs_m = monthlyize_last(pairs)
        return make_model(
            id_="cape", name="S&P 500 CAPE Ratio", category="valuation",
            desc="Shiller P/E.",
            pairs=pairs_m, direction=1,
            direction_note="High = expensive (+)",
            unit="×", value_fmt="{:.1f}",
            source_name=src, source_url=url,
            window_start="1990-01",
        )
    keep_or("cape", build_cape)

    # Price / Sales — unavailable
    models["ps"] = unavailable(
        "ps", "Price / Sales Ratio", "valuation",
        "S&P 500 price-to-sales.",
        "No free redistributable S&P 500 price-to-sales series found (index-level sales data is proprietary). Card left as a placeholder.",
        source_name="—",
        source_url="",
    )

    # Interest Rate Model: forward earnings yield − Baa
    def build_interest():
        fpe = load_hom_forward_pe()
        fpe_m = monthlyize_last(fpe)
        baa_by = {d[:7]: v for d, v in baa}
        pairs = []
        for d, pe in fpe_m:
            b = baa_by.get(d[:7])
            if b is None:
                # Baa monthly; allow previous month
                continue
            ey = 100.0 / pe  # percent
            pairs.append((d, ey - b))
        if len(pairs) < 20 and shiller:
            # fallback: Shiller earnings yield − Baa
            pairs = []
            for d, E, P in zip(shiller["date"], shiller["E"], shiller["P"]):
                if not E or not P or P == 0:
                    continue
                b = baa_by.get(d)
                if b is None:
                    continue
                pairs.append((d + "-01", (E / P) * 100.0 - b))
        return make_model(
            id_="interest", name="Interest Rate Model", category="valuation",
            desc="Forward earnings yield minus Moody's Baa yield (pp).",
            pairs=pairs, direction=-1,
            direction_note="High spread = stocks cheap vs bonds (−)",
            unit="pp", value_fmt="{:+.2f} pp",
            source_name="History of Market forward P/E (CC BY 4.0); Moody's Baa via FRED (BAA)",
            source_url="https://fred.stlouisfed.org/series/BAA",
            window_start="1990-01",
        )
    keep_or("interest", build_interest)

    # Mean Reversion: % above log-linear trend of Shiller P
    def build_meanrev():
        if not shiller:
            raise RuntimeError("Shiller unavailable")
        # Fit on 1871–present (or since 1990 for z-window); trend fit uses long history
        dates = shiller["date"]
        Ps = shiller["P"]
        xs, ys, keep = [], [], []
        for i, (d, P) in enumerate(zip(dates, Ps)):
            if P and P > 0:
                # months since 1871-01
                y, m = int(d[:4]), int(d[5:7])
                t = (y - 1871) * 12 + (m - 1)
                xs.append(float(t))
                ys.append(math.log(P))
                keep.append((d, P, t))
        a, b = linreg(xs, ys)
        pairs = []
        for d, P, t in keep:
            trend = math.exp(a + b * t)
            pairs.append((d + "-01", (P / trend - 1.0) * 100.0))
        return make_model(
            id_="meanrev", name="Mean Reversion", category="valuation",
            desc="S&P 500 vs exponential (log-linear) trend.",
            pairs=pairs, direction=1,
            direction_note="Above trend = expensive (+)",
            unit="% above trend", value_fmt="{:+.1f}%",
            source_name="S&P price from Robert Shiller (shillerdata.com); log-linear trend fit since 1871",
            source_url="https://shillerdata.com/",
            window_start="1990-01",
        )
    keep_or("meanrev", build_meanrev)

    # Earnings Yield Gap: Shiller E/P − GS10
    def build_eyg():
        if not shiller:
            raise RuntimeError("Shiller unavailable")
        gs_by = {d[:7]: v for d, v in gs10}
        # splice latest daily 10Y into current month if GS10 lags
        if dgs10:
            gs_by[dgs10[-1][0][:7]] = dgs10[-1][1]
        pairs = []
        for d, E, P in zip(shiller["date"], shiller["E"], shiller["P"]):
            if not E or not P or P == 0:
                continue
            g = gs_by.get(d)
            if g is None:
                continue
            pairs.append((d + "-01", (E / P) * 100.0 - g))
        return make_model(
            id_="eyg", name="Earnings Yield Gap", category="valuation",
            desc="Earnings yield minus 10Y yield.",
            pairs=pairs, direction=-1,
            direction_note="High gap = stocks cheap vs Treasuries (−)",
            unit="pp", value_fmt="{:+.2f} pp",
            source_name="Shiller earnings yield; 10Y Treasury via FRED (GS10 / DGS10)",
            source_url="https://fred.stlouisfed.org/series/GS10",
            window_start="1990-01",
        )
    keep_or("eyg", build_eyg)

    # ========== RECESSION ==========
    def build_yc():
        pairs = t10y2y  # daily; z-score on daily since 1990, display latest
        # Use monthly last for stabler z / sparkline
        pairs_m = monthlyize_last(pairs)
        return make_model(
            id_="yieldcurve", name="Yield Curve (10Y-2Y)", category="recession",
            desc="Yield curve spread.",
            pairs=pairs_m, direction=-1,
            direction_note="Inverted / low spread = recession risk (+)",
            unit="%", value_fmt="{:+.2f}%",
            source_name="FRED T10Y2Y (Board of Governors)",
            source_url="https://fred.stlouisfed.org/series/T10Y2Y",
            window_start="1990-01",
            # Override display with latest daily
            display_pairs=pairs,
            display_fmt="{:+.2f}%",
        )
    keep_or("yieldcurve", build_yc)

    def build_sahm():
        return make_model(
            id_="sahm", name="Sahm Rule", category="recession",
            desc="Unemployment trigger.",
            pairs=sahm, direction=1,
            direction_note="Above 0.50 = recession signal (+)",
            unit="pp", value_fmt="{:.2f}",
            source_name="Sahm Rule (real-time) via FRED SAHMREALTIME; Claudia Sahm",
            source_url="https://fred.stlouisfed.org/series/SAHMREALTIME",
            window_start="1990-01",
        )
    keep_or("sahm", build_sahm)

    def build_state():
        pairs = load_philly_state_decline()
        return make_model(
            id_="statecoin", name="State Coincidence", category="recession",
            desc="Share of states with a 3-month decline in the coincident index.",
            pairs=pairs, direction=1,
            direction_note="More states declining = recession risk (+)",
            unit="%", value_fmt="{:.0f}%",
            source_name="Philadelphia Fed state coincident indexes",
            source_url="https://www.philadelphiafed.org/surveys-and-data/regional-economic-analysis/state-coincident-indexes",
            window_start="1990-01",
        )
    keep_or("statecoin", build_state)

    # ========== SENTIMENT ==========
    def build_margin():
        levels = load_finra_margin()
        yoy = yoy_pct(levels)
        # z-score on YoY % (stationary); display latest level in $B
        disp = [(d, v / 1000.0) for d, v in levels]  # $ millions → $ billions
        return make_model(
            id_="margin", name="Margin Debt", category="sentiment",
            desc="NYSE/FINRA debit balances in customers' securities margin accounts.",
            pairs=yoy, direction=1,
            direction_note="High YoY growth = leverage / complacency (+)",
            unit="$B", value_fmt="{:.0f}",
            source_name="FINRA margin statistics",
            source_url="https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics",
            window_start="1998-01",
            display_pairs=disp,
            display_fmt="${:.0f}B",
        )
    keep_or("margin", build_margin)

    def build_junk():
        # Substitute: Moody's Baa − 10Y (GS10). ICE HY OAS is Pre-Approval Required.
        gs_by = {d[:7]: v for d, v in gs10}
        if dgs10:
            gs_by[dgs10[-1][0][:7]] = dgs10[-1][1]
        pairs = []
        for d, b in baa:
            g = gs_by.get(d[:7])
            if g is None:
                continue
            pairs.append((d, b - g))
        return make_model(
            id_="junk", name="Junk Bond Spreads", category="sentiment",
            desc="Credit risk premium (substitute series).",
            pairs=pairs, direction=-1,
            direction_note="Wide spreads = fear / contrarian bullish (−)",
            unit="pp", value_fmt="{:.2f} pp",
            source_name="Moody's Baa minus 10Y Treasury (FRED BAA − GS10)",
            source_url="https://fred.stlouisfed.org/series/BAA",
            window_start="1990-01",
            status="substitute",
            substitute_of="ICE BofA US High Yield OAS (FRED BAMLH0A0HYM2)",
            substitute_note="ICE BofA HY OAS is Copyrighted: Pre-Approval Required on FRED and is not redistributed. Using Moody's Baa − 10Y Treasury as a public credit-spread stand-in.",
        )
    keep_or("junk", build_junk)

    def build_vix():
        pairs_m = monthlyize_last(vix)
        return make_model(
            id_="vix", name="VIX Fear Index", category="sentiment",
            desc="CBOE Volatility Index.",
            pairs=pairs_m, direction=-1,
            direction_note="High VIX = fear / contrarian bullish (−)",
            unit="", value_fmt="{:.1f}",
            source_name="Cboe VIX via FRED VIXCLS (Copyrighted: Citation Required)",
            source_url="https://fred.stlouisfed.org/series/VIXCLS",
            window_start="1990-01",
            display_pairs=vix,
            display_fmt="{:.1f}",
        )
    keep_or("vix", build_vix)

    def build_epu():
        return make_model(
            id_="epu", name="Economic Policy Uncertainty", category="sentiment",
            desc="Baker, Bloom & Davis Index.",
            pairs=epu, direction=-1,
            direction_note="High uncertainty = fear / contrarian bullish (−)",
            unit="", value_fmt="{:.0f}",
            source_name="Baker, Bloom & Davis via FRED USEPUINDXM (public domain)",
            source_url="https://fred.stlouisfed.org/series/USEPUINDXM",
            window_start="1990-01",
        )
    keep_or("epu", build_epu)

    def build_consumer():
        return make_model(
            id_="consumer", name="Consumer Confidence", category="sentiment",
            desc="U of Michigan Index.",
            pairs=umcsent, direction=1,
            direction_note="High confidence = complacency (+); low = contrarian bullish (−)",
            unit="", value_fmt="{:.1f}",
            source_name="Surveys of Consumers, University of Michigan via FRED UMCSENT (Citation Required)",
            source_url="https://fred.stlouisfed.org/series/UMCSENT",
            window_start="1990-01",
        )
    keep_or("consumer", build_consumer)

    # Order
    order = [
        "buffett", "cape", "ps", "interest", "meanrev", "eyg",
        "yieldcurve", "sahm", "statecoin",
        "margin", "junk", "vix", "epu", "consumer",
    ]
    model_list = [models[i] for i in order if i in models]

    # Composite
    def avg_sd(cat):
        sds = [m["sd"] for m in model_list if m["category"] == cat and isinstance(m.get("sd"), (int, float))]
        return statistics.fmean(sds) if sds else 0.0

    val_avg = avg_sd("valuation")
    rec_avg = avg_sd("recession")
    sen_avg = avg_sd("sentiment")
    composite = val_avg * 0.5 + rec_avg * 0.25 + sen_avg * 0.25
    if composite <= -1.5:
        signal, signal_color = "STRONG BUY", "#00C853"
    elif composite <= -0.5:
        signal, signal_color = "BUY", "#69F0AE"
    elif composite <= 0.5:
        signal, signal_color = "HOLD", "#FFD600"
    elif composite <= 1.5:
        signal, signal_color = "SELL", "#FF8A65"
    else:
        signal, signal_color = "STRONG SELL", "#FF1744"

    # Recession probability — NY Fed preferred
    rec_prob = {
        "prob": None,
        "method": None,
        "as_of": None,
        "source_name": None,
        "source_url": "https://www.newyorkfed.org/research/capital_markets/ycfaq",
        "status": "unavailable",
        "history": [],
    }
    try:
        as_of, prob, hist = load_nyfed_recession_prob()
        rec_prob = {
            "prob": round(prob, 1),
            "method": "NY Fed yield-curve recession probability (probability of recession 12 months ahead)",
            "as_of": as_of,
            "source_name": "Federal Reserve Bank of New York",
            "source_url": "https://www.newyorkfed.org/research/capital_markets/ycfaq",
            "status": "ok",
            "history": [{"date": d[:7], "value": round(v, 2)} for d, v in hist if d[:7] >= f"{int(hist[-1][0][:4]) - 10:04d}"],
        }
    except Exception as e:
        errors.append(f"nyfed: {e}")
        print(f"[error] nyfed: {e}", file=sys.stderr)
        # Do NOT fall back to the old logistic with fake defaults — leave unavailable
        rec_prob["unavailable_reason"] = str(e)

    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    # prettier offset
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    payload = {
        "updated": now,
        "timezone": "America/New_York",
        "models": model_list,
        "composite": {
            "score": round(composite, 2),
            "signal": signal,
            "signal_color": signal_color,
            "valuation_avg": round(val_avg, 2),
            "recession_avg": round(rec_avg, 2),
            "sentiment_avg": round(sen_avg, 2),
            "weights": {"valuation": 0.50, "recession": 0.25, "sentiment": 0.25},
        },
        "recession_probability": rec_prob,
        "errors": errors,
        "notes": [
            "SD scores are z-scores vs each series' own history (typically since 1990), multiplied by a direction so +SD is bearish/expensive/risky for the composite, then capped at ±2.",
            "Weights: Valuation 50%, Recession 25%, Sentiment 25%.",
            "Signal map: ≤−1.5 Strong Buy; ≤−0.5 Buy; ≤0.5 Hold; ≤1.5 Sell; else Strong Sell.",
            "ICE BofA HY OAS is not redistributed (FRED Pre-Approval Required); Baa−10Y is the labeled substitute.",
            "Price/Sales has no free redistributable source and is shown as unavailable.",
        ],
    }
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="Same full rebuild today (daily job); reserved for future lighter path")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    payload = build_all(daily_only=args.daily)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2)
    # Stable trailing newline
    if not text.endswith("\n"):
        text += "\n"
    prev = args.out.read_text() if args.out.exists() else None
    # Compare without updated timestamp for CHANGED detection
    def strip_updated(s):
        try:
            o = json.loads(s)
            o.pop("updated", None)
            return json.dumps(o, sort_keys=True)
        except Exception:
            return s
    changed = prev is None or strip_updated(prev) != strip_updated(text)
    args.out.write_text(text)
    n_ok = sum(1 for m in payload["models"] if m["status"] in ("ok", "substitute"))
    n_na = sum(1 for m in payload["models"] if m["status"] == "unavailable")
    print(
        f"{'CHANGED' if changed else 'UNCHANGED'} "
        f"models_ok={n_ok} unavailable={n_na} "
        f"composite={payload['composite']['score']:+.2f} {payload['composite']['signal']} "
        f"rec_prob={payload['recession_probability'].get('prob')} "
        f"errors={len(payload['errors'])}"
    )
    for m in payload["models"]:
        print(
            f"  {m['id']:12s} {m['status']:12s} "
            f"val={m.get('value_display')} sd={m.get('sd')} as_of={m.get('as_of')}"
        )
    # exit 0 even with some model errors if we wrote a file; non-zero only if catastrophic
    if n_ok < 8:
        sys.exit("ERROR: fewer than 8 models succeeded")


if __name__ == "__main__":
    main()
