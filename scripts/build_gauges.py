#!/usr/bin/env python3
"""Build data/gauges.json for the Gauges tab.

Fetches public series, computes each of the 12 models' current value and
z-score (capped at +/-2), writes sparkline history, composite signal, and
NY Fed recession probability.

Mean Reversion (id meanrev) and Price/Sales (id ps) are retired. Full rebuilds
and --daily both omit them, drop a stale row from an existing gauges.json, and
do not include them in category or composite scores. Category weights stay
valuation 50%, recession 25%, sentiment 25%.

The S&P price chart is forward-only. Composite readings are appended to
data/gauge_signal_history.json on a successful build, one per calendar date.
Older readings are never replaced, and a partial or failed build does not
append. Prices before the first saved reading are context only.

Usage:
  python3 scripts/build_gauges.py           # full rebuild
  python3 scripts/build_gauges.py --daily   # full rebuild of the active set; does not restore retired gauges

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
  - Price/Sales: no free redistributable source; the placeholder is retired
  - S&P price line: Shiller monthly average (shillerdata.com), not the FRED
    SP500 series (S&P Dow Jones prohibits reproduction without permission)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
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
HISTORY = ROOT / "data" / "gauge_signal_history.json"
PRICE_START = "1996-01"  # same start as the Valuation & Macro charts
CACHE = Path(os.environ.get("GAUGES_CACHE", "/tmp/gauges-cache"))
CACHE.mkdir(parents=True, exist_ok=True)

# Make xlrd importable when running under system python3 on the shared box.
_VENV_SP = Path("/workspace/.pwvenv/lib/python3.13/site-packages")
if _VENV_SP.exists() and str(_VENV_SP) not in sys.path:
    sys.path.insert(0, str(_VENV_SP))

try:
    import xlrd
except ImportError:
    xlrd = None  # ensure_xlrd() re-execs or exits when a rebuild actually runs


def ensure_xlrd():
    """Import xlrd, re-exec into the shared venv if needed. Scoring does not call this."""
    global xlrd
    if xlrd is not None:
        return
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


# Retired gauges are never rebuilt and never restored from data/gauges.json
# (daily fallback or monthly full rebuild).
# meanrev = Mean Reversion. ps = Price / Sales Ratio (no redistributable series).
RETIRED_MODEL_IDS = frozenset({"meanrev", "ps"})

# Valuation, recession, sentiment. Scoring uses numeric sd values only.
MODEL_ORDER = (
    "buffett", "cape", "interest", "eyg",
    "yieldcurve", "sahm", "statecoin",
    "margin", "junk", "vix", "epu", "consumer",
)

CATEGORY_WEIGHTS = {"valuation": 0.50, "recession": 0.25, "sentiment": 0.25}


def cached_model(existing: dict | None, id_: str) -> dict | None:
    """Previous JSON row used when a fetch fails. Retired ids are never returned."""
    if not existing or id_ in RETIRED_MODEL_IDS:
        return None
    for model in existing.get("models") or []:
        if model.get("id") == id_ and model.get("id") not in RETIRED_MODEL_IDS:
            return model
    return None


def take_model(models: dict, existing: dict | None, id_: str, builder, errors: list[str]) -> None:
    """Build one gauge. On failure, reuse the previous row. Skip retired ids entirely."""
    if id_ in RETIRED_MODEL_IDS:
        return
    try:
        built = builder()
        built["_fresh"] = True
        models[id_] = built
    except Exception as e:
        errors.append(f"{id_}: {e}")
        print(f"[error] {id_}: {e}", file=sys.stderr)
        old = cached_model(existing, id_)
        if old:
            row = dict(old)
            row["_fresh"] = False
            models[id_] = row
            return
        missing = unavailable(id_, id_, "valuation", "", str(e))
        missing["_fresh"] = False
        models[id_] = missing


def active_models(models: list[dict] | dict[str, dict]) -> list[dict]:
    """Models in dashboard order, with retired ids removed."""
    if isinstance(models, dict):
        by_id = {i: m for i, m in models.items() if i not in RETIRED_MODEL_IDS}
    else:
        by_id = {}
        for model in models:
            mid = model.get("id")
            if mid and mid not in RETIRED_MODEL_IDS:
                by_id[mid] = model
    return [by_id[i] for i in MODEL_ORDER if i in by_id]


def category_sd_avg(model_list: list[dict], category: str) -> float:
    sds = [
        m["sd"] for m in model_list
        if m.get("category") == category and isinstance(m.get("sd"), (int, float))
    ]
    return statistics.fmean(sds) if sds else 0.0


def signal_for(composite: float) -> tuple[str, str]:
    if composite <= -1.5:
        return "STRONG BUY", "#00C853"
    if composite <= -0.5:
        return "BUY", "#69F0AE"
    if composite <= 0.5:
        return "HOLD", "#FFD600"
    if composite <= 1.5:
        return "SELL", "#FF8A65"
    return "STRONG SELL", "#FF1744"


def score_models(model_list: list[dict]) -> dict:
    """Category averages and composite. Weights stay 50/25/25. No network."""
    val_avg = category_sd_avg(model_list, "valuation")
    rec_avg = category_sd_avg(model_list, "recession")
    sen_avg = category_sd_avg(model_list, "sentiment")
    composite = (
        val_avg * CATEGORY_WEIGHTS["valuation"]
        + rec_avg * CATEGORY_WEIGHTS["recession"]
        + sen_avg * CATEGORY_WEIGHTS["sentiment"]
    )
    signal, signal_color = signal_for(composite)
    return {
        "score": round(composite, 2),
        "signal": signal,
        "signal_color": signal_color,
        "valuation_avg": round(val_avg, 2),
        "recession_avg": round(rec_avg, 2),
        "sentiment_avg": round(sen_avg, 2),
        "weights": {
            "valuation": CATEGORY_WEIGHTS["valuation"],
            "recession": CATEGORY_WEIGHTS["recession"],
            "sentiment": CATEGORY_WEIGHTS["sentiment"],
        },
    }


def rescore_payload(payload: dict) -> dict:
    """Drop retired gauges and recompute scores from the remaining rows. No network."""
    out = dict(payload)
    out["models"] = active_models(payload.get("models") or [])
    out["composite"] = score_models(out["models"])
    return out


def model_version() -> str:
    return "v1:" + ",".join(MODEL_ORDER)


def shiller_monthly_prices(shiller: dict | None) -> list[dict]:
    """Monthly average S&P price from an already loaded Shiller workbook. No network."""
    if not shiller:
        return []
    out = []
    for date, price in zip(shiller.get("date") or [], shiller.get("P") or []):
        if not isinstance(date, str) or date < PRICE_START:
            continue
        if not isinstance(price, (int, float)) or isinstance(price, bool) or not price > 0:
            continue
        out.append({"date": date[:7], "price": round(float(price), 2)})
    return merge_price_points([], out)


def merge_price_points(existing: list | None, incoming: list | None) -> list[dict]:
    """Append-only monthly prices. The first stored price for a month is kept."""
    by: dict[str, dict] = {}
    for src in (existing or []), (incoming or []):
        for point in src:
            if not isinstance(point, dict):
                continue
            date = str(point.get("date") or "")[:7]
            price = point.get("price")
            if len(date) != 7 or date[4] != "-":
                continue
            if not isinstance(price, (int, float)) or isinstance(price, bool) or not price > 0:
                continue
            if date not in by:
                by[date] = {"date": date, "price": round(float(price), 2)}
    return [by[d] for d in sorted(by)]


def price_on_or_before(prices: list[dict], signal_date: str) -> dict | None:
    """Latest stored month that was already on file by the signal date."""
    month = (signal_date or "")[:7]
    if len(month) != 7:
        return None
    eligible = [p for p in prices if p["date"] <= month]
    return eligible[-1] if eligible else None


def _valid_obs_date(value: str) -> str | None:
    date = str(value or "")[:10]
    if len(date) == 10 and date[4] == "-" and date[7] == "-":
        return date
    return None


def normalize_observations(observations: list | None) -> list[dict]:
    """Keep the first well-formed reading for each date. No network."""
    kept: list[dict] = []
    seen: set[str] = set()
    for obs in observations or []:
        if not isinstance(obs, dict):
            continue
        date = _valid_obs_date(str(obs.get("date") or ""))
        score = obs.get("score")
        if date is None or date in seen:
            continue
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        if not obs.get("signal"):
            continue
        row = dict(obs)
        row["date"] = date
        kept.append(row)
        seen.add(date)
    kept.sort(key=lambda row: row["date"])
    return kept


def make_signal_snapshot(payload: dict, price_point: dict | None, *, fresh_count: int, min_fresh: int = 8) -> dict | None:
    """A new dated reading, or None when the build is partial, stale, or priceless.

    Callers pass fresh_count from models built in this run. A cache fallback
    does not count. Errors mean at least one input was not rebuilt, so the
    reading is not appended.
    """
    if payload.get("errors"):
        return None
    if fresh_count < min_fresh:
        return None
    comp = payload.get("composite") or {}
    score = comp.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not comp.get("signal"):
        return None
    if not price_point or not isinstance(price_point.get("price"), (int, float)):
        return None
    date = _valid_obs_date(str(payload.get("updated") or ""))
    if date is None:
        return None
    inputs = {}
    for model in payload.get("models") or []:
        mid = model.get("id")
        if mid and mid not in RETIRED_MODEL_IDS:
            inputs[mid] = model.get("as_of")
    return {
        "date": date,
        "observed_at": payload.get("updated"),
        "score": score,
        "signal": comp["signal"],
        "signal_color": comp.get("signal_color"),
        "valuation_avg": comp.get("valuation_avg"),
        "recession_avg": comp.get("recession_avg"),
        "sentiment_avg": comp.get("sentiment_avg"),
        "weights": comp.get("weights") or dict(CATEGORY_WEIGHTS),
        "price": price_point["price"],
        "price_as_of": price_point["date"],
        "price_kind": "shiller_monthly_average",
        "model_version": model_version(),
        "constituent_ids": list(MODEL_ORDER),
        "inputs_as_of": inputs,
    }


def merge_signal_history(existing: dict | None, snapshot: dict | None) -> dict:
    """Append a new date. Never replace a stored date or insert an earlier one."""
    existing = existing or {}
    observations = normalize_observations(existing.get("observations"))
    dates = {row["date"] for row in observations}
    if snapshot and isinstance(snapshot, dict):
        date = _valid_obs_date(str(snapshot.get("date") or ""))
        score = snapshot.get("score")
        if (
            date is not None
            and snapshot.get("signal")
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
            and date not in dates
            and (not dates or date > max(dates))
        ):
            row = dict(snapshot)
            row["date"] = date
            observations.append(row)
    tracking = observations[0]["date"] if observations else None
    return {
        "model_version": model_version(),
        "constituent_ids": list(MODEL_ORDER),
        "weights": {
            "valuation": CATEGORY_WEIGHTS["valuation"],
            "recession": CATEGORY_WEIGHTS["recession"],
            "sentiment": CATEGORY_WEIGHTS["sentiment"],
        },
        "tracking_started": tracking,
        "history_unavailable_before": tracking,
        "observations": observations,
        "notes": [
            "Append-only record of composite readings this builder actually saved.",
            "Nothing before tracking_started is reconstructed. The live z-score uses each series' full sample through the latest point, so a past reading of that same statistic would use information that was not yet known.",
            "Several inputs (GDP, Z.1 equities, Shiller, Philly Fed state indexes, Sahm) are revised vintages. This file does not treat them as point-in-time.",
            "A second run on a stored date does not replace that reading. A failed or partial rebuild does not append.",
            "price is the Shiller monthly average then on file (price_as_of), not a daily close and not an intraday quote.",
        ],
    }


def price_chart_block(prices: list[dict], history: dict) -> dict:
    tracking = history.get("tracking_started")
    return {
        "title": "S&P 500 and the saved composite signal",
        "frequency": "monthly",
        "price_kind": "shiller_monthly_average",
        "source_name": "Robert J. Shiller, Irrational Exuberance data (monthly average price)",
        "source_url": "https://shillerdata.com/",
        "tracking_started": tracking,
        "history_unavailable_before": tracking,
        "disclaimer": (
            "Signal tracking starts on the first date a composite was saved. "
            "Prices before that are shown in gray because no composite history was saved, and none is estimated. "
            "The line is Shiller's monthly average S&P price as first stored here, not a daily close and not an intraday quote. "
            "Stored months are not rewritten when Shiller revises them. "
            "Colored marks are saved gauge readings at the price then on file. "
            "This chart illustrates those readings. It is not a backtest of strategy returns."
        ),
        "prices": prices,
        "observations": history.get("observations") or [],
    }


def apply_signal_tracking(
    payload: dict,
    prev: dict | None,
    history: dict | None,
    prices_incoming: list | None = None,
) -> tuple[dict, dict]:
    """Attach the price chart and append today's reading when the build is healthy.

    Does not fetch. Previous prices and observations are kept. A missing
    fresh-id list means the caller does not know this run rebuilt the inputs,
    so no reading is appended.
    """
    payload = dict(payload)
    fresh_ids = payload.pop("_fresh_ids", None)
    incoming = payload.pop("_prices_incoming", None)
    if prices_incoming is not None:
        incoming = prices_incoming
    prev_chart = (prev or {}).get("price_chart") or {}
    prices = merge_price_points(prev_chart.get("prices") or [], incoming or [])
    if history and history.get("observations"):
        hist_in = history
    else:
        hist_in = {"observations": prev_chart.get("observations") or []}
    snapshot = None
    if fresh_ids is not None:
        updated = str(payload.get("updated") or "")
        snapshot = make_signal_snapshot(
            payload,
            price_on_or_before(prices, updated[:10]),
            fresh_count=len(fresh_ids),
        )
    merged = merge_signal_history(hist_in, snapshot)
    payload["price_chart"] = price_chart_block(prices, merged)
    return payload, merged


def load_signal_history(path: Path | None = None) -> dict:
    path = path or HISTORY
    if not path.exists():
        return {"observations": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"observations": []}
    return data if isinstance(data, dict) else {"observations": []}


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def build_all(daily_only: bool = False) -> dict:
    """Build full gauges payload.

    --daily still rebuilds every active model. An existing file is only a
    fallback when a fetch fails. Retired ids are never built and never restored
    from that fallback, on the daily run or the monthly full rebuild.
    """
    ensure_xlrd()
    existing = None
    if daily_only and OUT.exists():
        try:
            existing = json.loads(OUT.read_text())
        except Exception:
            existing = None

    errors: list[str] = []
    models: dict[str, dict] = {}

    def keep_or(id_, builder):
        take_model(models, existing, id_, builder, errors)

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

    # Drop retired ids even if a stale gauges.json row was assigned directly.
    for retired_id in RETIRED_MODEL_IDS:
        models.pop(retired_id, None)
    fresh_ids = [mid for mid, model in models.items() if model.pop("_fresh", False)]
    model_list = active_models(models)
    comp = score_models(model_list)

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
        "composite": comp,
        "recession_probability": rec_prob,
        "errors": errors,
        "notes": [
            "SD scores are z-scores vs each series' own history (typically since 1990), multiplied by a direction so +SD is bearish/expensive/risky for the composite, then capped at ±2.",
            "Weights: Valuation 50%, Recession 25%, Sentiment 25%.",
            "Signal map: ≤−1.5 Strong Buy; ≤−0.5 Buy; ≤0.5 Hold; ≤1.5 Sell; else Strong Sell.",
            "ICE BofA HY OAS is not redistributed (FRED Pre-Approval Required); Baa−10Y is the labeled substitute.",
            "Price/Sales had no free redistributable source and has been removed. It is not scored.",
            "The S&P price chart colors only composite readings saved in gauge_signal_history.json. Earlier prices are context. No historical signal is estimated.",
        ],
        "_fresh_ids": fresh_ids,
        "_prices_incoming": shiller_monthly_prices(shiller),
    }
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="Full rebuild of the active set; do not restore retired gauges from the existing JSON")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--history", type=Path, default=HISTORY)
    args = ap.parse_args()
    payload = build_all(daily_only=args.daily)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    prev_payload = None
    if args.out.exists():
        try:
            prev_payload = json.loads(args.out.read_text())
        except Exception:
            prev_payload = None
    # Append only after a healthy rebuild. History is loaded first so a write
    # cannot drop older readings, including when the price fetch failed.
    payload, history = apply_signal_tracking(payload, prev_payload, load_signal_history(args.history), None)
    text = json.dumps(payload, indent=2)
    if not text.endswith("\n"):
        text += "\n"
    hist_text = json.dumps(history, indent=2)
    if not hist_text.endswith("\n"):
        hist_text += "\n"
    prev = args.out.read_text() if args.out.exists() else None

    def strip_updated(s):
        try:
            o = json.loads(s)
            o.pop("updated", None)
            return json.dumps(o, sort_keys=True)
        except Exception:
            return s
    changed = prev is None or strip_updated(prev) != strip_updated(text)
    prev_hist = args.history.read_text() if args.history.exists() else None
    if prev_hist != hist_text:
        changed = True
    atomic_write(args.history, hist_text)
    atomic_write(args.out, text)
    n_ok = sum(1 for m in payload["models"] if m["status"] in ("ok", "substitute"))
    n_na = sum(1 for m in payload["models"] if m["status"] == "unavailable")
    n_sig = len((payload.get("price_chart") or {}).get("observations") or [])
    print(
        f"{'CHANGED' if changed else 'UNCHANGED'} "
        f"models_ok={n_ok} unavailable={n_na} "
        f"composite={payload['composite']['score']:+.2f} {payload['composite']['signal']} "
        f"signals={n_sig} "
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
