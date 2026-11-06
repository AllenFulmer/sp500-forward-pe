#!/usr/bin/env python3
"""Score and retirement checks for the Gauges tab. No source fetches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_gauges as bg  # noqa: E402

FETCHES: list[str] = []


def _blocked_curl(*args, **kwargs):
    FETCHES.append(repr(args[:1]))
    raise AssertionError("source fetch is not allowed in this test")


bg.curl = _blocked_curl

DATA = ROOT / "data" / "gauges.json"
HTML = ROOT / "gauges.html"
WORKFLOW = ROOT / ".github" / "workflows" / "gauges-daily.yml"
BUILDER = ROOT / "scripts" / "build_gauges.py"


def fail(msg: str) -> None:
    raise SystemExit("FAIL: " + msg)


def main() -> None:
    if FETCHES:
        fail("import fetched sources")

    if "meanrev" not in bg.RETIRED_MODEL_IDS or "ps" not in bg.RETIRED_MODEL_IDS:
        fail(f"retired set is {bg.RETIRED_MODEL_IDS}")
    if "meanrev" in bg.MODEL_ORDER or "ps" in bg.MODEL_ORDER:
        fail("a retired id is still in MODEL_ORDER")
    if len(bg.MODEL_ORDER) != 12:
        fail(f"expected 12 models, got {len(bg.MODEL_ORDER)}")
    if bg.CATEGORY_WEIGHTS != {"valuation": 0.50, "recession": 0.25, "sentiment": 0.25}:
        fail(f"weights changed: {bg.CATEGORY_WEIGHTS}")

    src = BUILDER.read_text()
    if "def build_meanrev" in src or 'keep_or("meanrev"' in src or "def linreg" in src:
        fail("mean reversion builder is still in build_gauges.py")
    if 'models["ps"]' in src:
        fail("price/sales placeholder is still built")
    if "models.pop(retired_id, None)" not in src or "active_models(models)" not in src:
        fail("build path no longer strips retired ids")
    if "take_model(models, existing" not in src:
        fail("daily/full rebuild no longer goes through take_model")

    html = HTML.read_text()
    if "Fourteen" in html or "Thirteen" in html or "Mean Reversion" in html or "Price / Sales" in html:
        fail("gauges.html still presents a removed gauge or the old count")
    if "Twelve valuation, recession, and sentiment models." not in html:
        fail("gauges.html count was not updated to twelve")
    if 'new Set(["meanrev", "ps"])' not in html:
        fail("gauges.html does not filter stale meanrev and ps rows")
    if 'id="price-chart"' not in html or "not a backtest of strategy returns" not in html:
        fail("price chart or its disclaimer is missing")
    if "shown in gray" not in html or "No composite history is estimated" not in html:
        fail("price chart does not say pre-tracking history is unavailable")

    workflow = WORKFLOW.read_text()
    if "python3 scripts/build_gauges.py --daily" not in workflow:
        fail("daily workflow no longer runs the builder")
    if "meanrev" not in workflow or "ps" not in workflow:
        fail("daily workflow does not document that retired gauges stay retired")
    if "data/gauge_signal_history.json" not in workflow:
        fail("daily workflow does not keep the signal history file")

    raw = json.loads(DATA.read_text())
    ids = [m["id"] for m in raw["models"]]
    if "meanrev" in ids or "ps" in ids or any(m.get("name") in ("Mean Reversion", "Price / Sales Ratio") for m in raw["models"]):
        fail("data/gauges.json still contains a retired gauge")
    if ids != list(bg.MODEL_ORDER):
        fail(f"model order drifted: {ids}")

    # Rescore must be a no-op on the published file and must not fetch.
    rescored = bg.rescore_payload(raw)
    if rescored["models"] != raw["models"]:
        fail("rescore changed published models")
    if rescored["composite"] != raw["composite"]:
        fail(f"published composite {raw['composite']} != rescored {rescored['composite']}")
    if raw["updated"] != rescored["updated"]:
        fail("rescore touched the data timestamp")
    if raw["recession_probability"] != rescored["recession_probability"]:
        fail("rescore touched recession probability")

    comp = raw["composite"]
    if comp["weights"] != {"valuation": 0.5, "recession": 0.25, "sentiment": 0.25}:
        fail(f"published weights {comp['weights']}")
    if comp["signal"] != "SELL" or comp["score"] != 0.52:
        fail(f"unexpected composite {comp['score']} {comp['signal']}")
    if comp["valuation_avg"] != 1.23 or comp["recession_avg"] != -0.01 or comp["sentiment_avg"] != -0.37:
        fail(f"unexpected category averages {comp}")

    # Same rows plus a stale Mean Reversion card must be dropped before scoring.
    stale = json.loads(DATA.read_text())
    stale["models"].insert(4, {
        "id": "meanrev",
        "name": "Mean Reversion",
        "category": "valuation",
        "sd": 2.0,
        "status": "ok",
    })
    cleaned = bg.rescore_payload(stale)
    if any(m["id"] == "meanrev" for m in cleaned["models"]):
        fail("rescore kept meanrev")
    if cleaned["composite"] != comp:
        fail("stale meanrev changed the composite")
    # A numeric sd on the retired Price/Sales row must not enter the average.
    stale_ps = json.loads(DATA.read_text())
    stale_ps["models"].append({
        "id": "ps",
        "name": "Price / Sales Ratio",
        "category": "valuation",
        "sd": 2.0,
        "status": "ok",
    })
    without_ps = bg.rescore_payload(stale_ps)
    if any(m["id"] == "ps" for m in without_ps["models"]):
        fail("rescore kept ps")
    if without_ps["composite"] != comp:
        fail("a stale ps sd changed the composite")
    # Scoring the stale row directly (the old 5-gauge valuation average) is the
    # pre-removal composite, which confirms the +2 SD input is what we removed.
    with_meanrev = [m for m in stale["models"] if m["id"] != "ps"]
    old = bg.score_models(with_meanrev)
    if old["valuation_avg"] != 1.38 or old["score"] != 0.59:
        fail(f"old composite math drifted: {old}")

    # Daily/monthly fallback: every fetch fails, previous JSON is reused,
    # except the retired gauge, which is not restored.
    called = []

    def boom():
        called.append("fetch")
        raise RuntimeError("offline")

    models: dict = {}
    errors: list[str] = []
    existing = {"models": raw["models"] + [{
        "id": "meanrev",
        "name": "Mean Reversion",
        "category": "valuation",
        "sd": 2.0,
        "status": "ok",
    }]}
    existing["models"].append({
        "id": "ps", "name": "Price / Sales Ratio", "category": "valuation",
        "sd": 2.0, "status": "ok",
    })
    for id_ in list(bg.MODEL_ORDER) + ["meanrev", "ps"]:
        bg.take_model(models, existing, id_, boom, errors)
    # A successful builder for the retired id is ignored too.
    built = {"ran": False}

    def sneak():
        built["ran"] = True
        return {"id": "meanrev", "category": "valuation", "sd": 2.0, "status": "ok"}

    bg.take_model(models, existing, "meanrev", sneak, errors)
    if built["ran"]:
        fail("retired builder was invoked")
    if bg.cached_model(existing, "meanrev") is not None or bg.cached_model(existing, "ps") is not None:
        fail("cached_model returned a retired gauge")
    for retired_id in bg.RETIRED_MODEL_IDS:
        models.pop(retired_id, None)
    restored = bg.active_models(models)
    if [m["id"] for m in restored] != list(bg.MODEL_ORDER):
        fail(f"fallback restore drifted: {[m['id'] for m in restored]}")
    if bg.score_models(restored) != comp:
        fail("fallback composite changed")
    if len(called) != len(bg.MODEL_ORDER):
        fail(f"expected one offline attempt per active model, got {len(called)}")
    if FETCHES:
        fail(f"curl was called: {FETCHES}")

    # Forward-only signal history. No network, no backfill, no overwrite.
    price = {"date": "2026-09", "price": 1000.0}
    snap = bg.make_signal_snapshot(raw, price, fresh_count=len(raw["models"]))
    if snap is None or snap["date"] != raw["updated"][:10] or snap["score"] != comp["score"]:
        fail(f"snapshot of the published reading failed: {snap}")
    if snap["constituent_ids"] != list(bg.MODEL_ORDER):
        fail("snapshot constituents drifted")
    if snap["price_as_of"] != "2026-09" or snap["price_kind"] != "shiller_monthly_average":
        fail("snapshot price was not the dated Shiller month")
    if bg.make_signal_snapshot(dict(raw, errors=["stale"]), price, fresh_count=12) is not None:
        fail("a failed build produced a snapshot")
    if bg.make_signal_snapshot(raw, price, fresh_count=3) is not None:
        fail("a thin fresh set produced a snapshot")
    if bg.make_signal_snapshot(raw, None, fresh_count=12) is not None:
        fail("a reading without a price was stored")
    history = bg.merge_signal_history({"observations": []}, snap)
    again = bg.merge_signal_history(history, dict(snap, score=9.9, signal="STRONG SELL"))
    if again["observations"] != history["observations"]:
        fail("a same-day rebuild overwrote the stored signal")
    older = dict(snap, date="2020-01-15", score=0.0, signal="HOLD")
    if bg.merge_signal_history(history, older)["observations"] != history["observations"]:
        fail("an earlier date was inserted into signal history")
    nxt = dict(snap, date="2026-11-07", observed_at="2026-11-07T21:00:00+00:00", score=0.40, signal="HOLD")
    grown = bg.merge_signal_history(history, nxt)
    if [row["date"] for row in grown["observations"]] != [snap["date"], "2026-11-07"]:
        fail("a later date was not appended")
    if grown["observations"][0] != history["observations"][0]:
        fail("appending changed the original reading")
    if grown["tracking_started"] != snap["date"]:
        fail("tracking start moved")
    prices = bg.merge_price_points(
        [{"date": "2020-01", "price": 100.0}, {"date": "2020-02", "price": 101.0}],
        [{"date": "2020-01", "price": 999.0}, {"date": "2020-03", "price": 102.0}],
    )
    if prices != [
        {"date": "2020-01", "price": 100.0},
        {"date": "2020-02", "price": 101.0},
        {"date": "2020-03", "price": 102.0},
    ]:
        fail(f"price merge rewrote history: {prices}")
    chart, merged = bg.apply_signal_tracking(
        dict(raw, _fresh_ids=list(bg.MODEL_ORDER), _prices_incoming=[{"date": "2026-10", "price": 1100.0}]),
        {"price_chart": {"prices": [{"date": "2026-09", "price": 1000.0}], "observations": history["observations"]}},
        history,
    )
    if any(p["date"] == "2026-09" and p["price"] != 1000.0 for p in chart["price_chart"]["prices"]):
        fail("apply_signal_tracking rewrote a stored price")
    if chart["price_chart"]["observations"] != history["observations"]:
        fail("apply_signal_tracking changed stored signals when the date already exists")
    if "_fresh_ids" in chart or "_prices_incoming" in chart:
        fail("private build keys leaked into the payload")
    if merged["observations"] != history["observations"]:
        fail("history file copy diverged")

    published_chart = raw.get("price_chart") or {}
    pub_obs = published_chart.get("observations") or []
    if len(pub_obs) < 1:
        fail("published chart has no saved signal")
    if pub_obs[0]["date"] != published_chart.get("tracking_started"):
        fail("tracking start is not the first saved signal")
    if any(row["date"] < published_chart["tracking_started"] for row in pub_obs):
        fail("a signal is dated before tracking starts")
    if pub_obs[0]["score"] != comp["score"] or pub_obs[0]["signal"] != comp["signal"]:
        fail("first saved signal does not match the published composite")
    if pub_obs[0].get("price_kind") != "shiller_monthly_average":
        fail("saved price is not labeled as a Shiller monthly average")
    if not pub_obs[0].get("price_as_of") or pub_obs[0]["price_as_of"] > pub_obs[0]["date"][:7]:
        fail("saved price as-of is after the signal date")
    price_dates = [p["date"] for p in published_chart.get("prices") or []]
    if price_dates != sorted(set(price_dates)) or not price_dates or price_dates[0] < "1996-01":
        fail("published price series is missing, unsorted, or starts before the chart window")
    hist_path = ROOT / "data" / "gauge_signal_history.json"
    hist_file = json.loads(hist_path.read_text())
    if hist_file.get("observations") != pub_obs:
        fail("signal history file does not match the chart observations")
    if "not a backtest" not in (published_chart.get("disclaimer") or ""):
        fail("price chart disclaimer is missing")

    print(
        f"OK models={len(raw['models'])} "
        f"composite={comp['score']:+.2f} {comp['signal']} "
        f"valuation={comp['valuation_avg']:+.2f} "
        f"recession={comp['recession_avg']:+.2f} "
        f"sentiment={comp['sentiment_avg']:+.2f}"
    )


if __name__ == "__main__":
    main()
