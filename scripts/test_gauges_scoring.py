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

    if "meanrev" not in bg.RETIRED_MODEL_IDS:
        fail("meanrev is not retired")
    if "meanrev" in bg.MODEL_ORDER:
        fail("meanrev is still in MODEL_ORDER")
    if len(bg.MODEL_ORDER) != 13:
        fail(f"expected 13 models, got {len(bg.MODEL_ORDER)}")
    if bg.CATEGORY_WEIGHTS != {"valuation": 0.50, "recession": 0.25, "sentiment": 0.25}:
        fail(f"weights changed: {bg.CATEGORY_WEIGHTS}")

    src = BUILDER.read_text()
    if "def build_meanrev" in src or 'keep_or("meanrev"' in src or "def linreg" in src:
        fail("mean reversion builder is still in build_gauges.py")
    if "models.pop(retired_id, None)" not in src or "active_models(models)" not in src:
        fail("build path no longer strips retired ids")
    if "take_model(models, existing" not in src:
        fail("daily/full rebuild no longer goes through take_model")

    html = HTML.read_text()
    if "Fourteen" in html or "Mean Reversion" in html:
        fail("gauges.html still presents Mean Reversion or the old count")
    if "Thirteen valuation, recession, and sentiment models." not in html:
        fail("gauges.html count was not updated to thirteen")
    if 'm.id !== "meanrev"' not in html:
        fail("gauges.html does not filter a stale meanrev row")

    workflow = WORKFLOW.read_text()
    if "python3 scripts/build_gauges.py --daily" not in workflow:
        fail("daily workflow no longer runs the builder")
    if "meanrev" not in workflow:
        fail("daily workflow does not document that meanrev stays retired")

    raw = json.loads(DATA.read_text())
    ids = [m["id"] for m in raw["models"]]
    if "meanrev" in ids or any(m.get("name") == "Mean Reversion" for m in raw["models"]):
        fail("data/gauges.json still contains Mean Reversion")
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
    for id_ in list(bg.MODEL_ORDER) + ["meanrev"]:
        bg.take_model(models, existing, id_, boom, errors)
    # A successful builder for the retired id is ignored too.
    built = {"ran": False}

    def sneak():
        built["ran"] = True
        return {"id": "meanrev", "category": "valuation", "sd": 2.0, "status": "ok"}

    bg.take_model(models, existing, "meanrev", sneak, errors)
    if built["ran"]:
        fail("retired builder was invoked")
    if bg.cached_model(existing, "meanrev") is not None:
        fail("cached_model returned meanrev")
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

    print(
        f"OK models={len(raw['models'])} "
        f"composite={comp['score']:+.2f} {comp['signal']} "
        f"valuation={comp['valuation_avg']:+.2f} "
        f"recession={comp['recession_avg']:+.2f} "
        f"sentiment={comp['sentiment_avg']:+.2f}"
    )


if __name__ == "__main__":
    main()
