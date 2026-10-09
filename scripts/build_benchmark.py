"""Build the frozen benchmark: planted incidents, natural cases and quiet weeks (rules in data/BENCHMARK_GUIDE.md).

    python -m scripts.build_benchmark

Writes data/benchmark.json. Run once, before any investigator prompt exists; the file's hash goes into
data/manifest.json. Dev and held-out draw from separate random streams, so held-out parameters are never seen
during development.
"""
import datetime as dt
import hashlib
import json
import pathlib
import random
import sys

import duckdb

from wdid import health, incidents
from wdid.engine import Engine, week_of
from wdid.semantic import METRICS

ROOT = pathlib.Path(__file__).resolve().parent.parent
SEED = 20261010
D = dt.date
PLANT_WEEKS = [D(2020, 12, 7) + dt.timedelta(days=7 * k) for k in range(6)]        # 7 Dec - 11 Jan
CART_FROM = D(2020, 12, 7)
QUIET_WEEKS = [D(2020, 12, 7) + dt.timedelta(days=7 * k) for k in range(8) if k != 6]   # 7 Dec - 25 Jan, not 18 Jan
PLACEHOLDERS = {"<Other>", "(not set)", "(data deleted)", "", None}

STEP_METRIC = {"purchase": "checkout_completion_rate", "begin_checkout": "checkout_start_rate", "add_to_cart": "cart_rate"}
CFG = {
    "product_break": {"dims": ["device", "os", "browser", "country"], "questions": ["conversion_rate", "conversion_rate", "revenue"],
                      "size": ("fraction", 0.5, 1.0), "small": ("fraction", 0.05, 0.15)},
    "traffic_loss": {"dims": ["medium", "source", "country"], "direct": "sessions", "questions": ["revenue", "sessions"],
                     "size": ("fraction", 0.4, 0.9), "small": ("fraction", 0.02, 0.06)},
    "mix_shift": {"dims": ["country", "source", "browser"], "direct": "sessions", "questions": ["conversion_rate"],
                  "size": ("fraction", 0.6, 1.0), "small": ("fraction", 0.03, 0.08)},
    "tracking_dup": {"dims": ["browser", "os", "device", "country"], "check": "duplicate_purchase_events",
                     "questions": ["revenue", "aov"]},
    "tracking_gap": {"dims": ["browser", "os", "device"], "check": "purchases_without_checkout",
                     "questions": ["checkout_start_rate"], "size": ("fraction", 0.85, 1.0)},
    "basket_change": {"dims": ["country", "device", "os", "user_type"], "direct": "aov", "questions": ["revenue", "aov"],
                      "size": ("factor", 0.35, 0.7), "small": ("factor", 0.92, 0.98)},
    "landing_break": {"dims": ["landing"], "direct": "view_item_rate", "questions": ["conversion_rate"],
                      "size": ("fraction", 0.6, 1.0)},
    "units_bug": {"dims": ["country"], "check": "revenue_per_order_jump", "questions": ["revenue"]},
}
PLANTED = {
    "dev": {"clear": ["product_break"] * 5 + ["traffic_loss"] * 3 + ["mix_shift"] * 3 + ["tracking_dup"] * 2
                     + ["tracking_gap"] * 2 + ["basket_change"] * 3,
            "too_small": ["product_break", "basket_change"]},
    "holdout": {"clear": ["product_break"] * 6 + ["traffic_loss"] * 4 + ["mix_shift"] * 3 + ["tracking_dup"] * 2
                         + ["tracking_gap"] * 2 + ["basket_change"] * 3 + ["landing_break"] * 2 + ["units_bug"] * 2,
                "too_small": ["traffic_loss", "product_break"]},
}
QUIET = {"dev": 8, "holdout": 10}
# Natural cases, labelled by hand from data/natural_weeks.json and the daily series (see the guide's table)
NATURAL = {
    "dev": [
        {"kind": "natural", "question": {"metric": "add_to_cart_rate", "week": "2020-11-16"},
         "expected": {"verdict": "incident", "label": "clear", "type": "tracking_issue", "segment": {}, "start": "2020-11-16",
                      "check_start": True},
         "note": "add_to_cart was not recorded before 16 Nov; the jump is the tag starting to fire, not shoppers"},
        {"kind": "broad", "question": {"metric": "conversion_rate", "week": "2020-12-21"},
         "expected": {"verdict": "broad", "label": "broad", "accept": ["broad"]},
         "note": "Christmas week: conversion -36% (beyond normal variation), no segment distinct from the rest"},
    ],
    "holdout": [
        {"kind": "natural", "question": {"metric": "revenue", "week": "2020-11-16"},
         "expected": {"verdict": "incident", "label": "clear", "type": "tracking_issue", "segment": {}, "start": "2020-11-19",
                      "check_start": True},
         "note": "purchase events fire about twice per order from 19 Nov (1.0 until 17 Nov), inflating revenue +61%"},
        {"kind": "natural", "question": {"metric": "revenue", "week": "2021-01-25"},
         "expected": {"verdict": "incident", "label": "clear", "type": "tracking_issue", "segment": {}, "start": "2021-01-26",
                      "check_start": True},
         "note": "from 26 Jan orders are recorded with zero revenue (69% of purchases that week); orders continue"},
        {"kind": "broad", "question": {"metric": "conversion_rate", "week": "2020-12-28"},
         "expected": {"verdict": "broad", "label": "broad", "accept": ["broad"]},
         "note": "New Year week: conversion -36% (beyond normal variation), no segment distinct from the rest"},
        {"kind": "broad", "question": {"metric": "revenue", "week": "2020-12-21"},
         "expected": {"verdict": "broad", "label": "broad", "accept": ["broad"]},
         "note": "Christmas week: revenue -69% (beyond normal variation), no segment distinct from the rest"},
    ],
}
# which health findings make a metric untrustworthy in a week (for choosing quiet cases)
TOUCHES = {"duplicate_purchase_events": {"revenue", "aov", "revenue_per_session"},
           "zero_revenue_orders": {"revenue", "aov", "revenue_per_session"},
           "revenue_per_order_jump": {"revenue", "aov", "revenue_per_session"},
           "purchases_without_checkout": {"checkout_start_rate", "checkout_completion_rate"}}
EVENT_METRICS = {"add_to_cart": {"cart_rate", "add_to_cart_rate", "checkout_start_rate"},
                 "begin_checkout": {"checkout_start_rate", "checkout_completion_rate"},
                 "view_item": {"view_item_rate", "cart_rate"}, "add_payment_info": set(), "page_view": set()}


def natural_distinct(survey, week: D, metric: str) -> set[str]:
    w = next(x for x in survey if x["week"] == str(week))
    return {list(d["segment"])[0] for d in w["metrics"].get(metric, {}).get("distinct_segments", [])}


def touched_by_health(survey, week: D) -> set[str]:
    """Metrics that a real tracking finding makes untrustworthy in this week."""
    w = next(x for x in survey if x["week"] == str(week))
    touched = set()
    for f in w["health"]:
        touched |= TOUCHES.get(f["check"], set()) | EVENT_METRICS.get(f.get("event"), set())
    return touched


def eligible(e: Engine, week: D, dims: list[str]) -> list[dict]:
    cur = week_of(week)
    out = []
    for dim in dims:
        rows = e.con.execute(f"""SELECT {dim}, count(*) * 1.0 / sum(count(*)) OVER () FROM base_sessions
                                 WHERE {cur.sql()} GROUP BY 1""").fetchall()
        out += [{dim: v} for v, share in rows if share >= 0.05 and v not in PLACEHOLDERS]
    return out


def oracle(e: Engine, inc: dict, cur, base) -> dict:
    cfg = CFG[inc["type"]]
    seg = inc["segment"]
    if "check" in cfg:
        found = [f for f in health.check(e, cur, base) if f["check"] == cfg["check"] and f["segment"] == seg]
        return {"by": cfg["check"], "fires": bool(found), "z": None,
                "detail": found[0]["detail"] if found else None}
    metric = STEP_METRIC.get(inc.get("step"), cfg.get("direct"))
    r = e.versus_rest(metric, seg, cur, base)
    return {"by": metric, "z": r["z"], "relative_to_rest": r["relative_to_rest"], "distinct": r["distinct"]}


def draw_planted(con, e: Engine, survey, rng: random.Random, typ: str, label: str, split: str) -> dict:
    cfg = CFG[typ]
    for _ in range(400):
        week = rng.choice(PLANT_WEEKS)
        cur, base = week_of(week), week_of(week - dt.timedelta(days=7))
        q = rng.choice(cfg["questions"])
        inc = {"type": typ, "seed": rng.randrange(1, 10**6)}
        if typ == "product_break":
            inc["step"] = rng.choice(["purchase", "purchase", "begin_checkout", "add_to_cart"])
            if inc["step"] == "add_to_cart" and week < CART_FROM:
                continue
        if typ == "landing_break":
            inc["step"] = "view_item"
        direct = STEP_METRIC.get(inc.get("step"), cfg.get("direct"))
        if {q, direct} & touched_by_health(survey, week):
            continue     # a real tracking finding touches this metric this week: the case would be ambiguous
        segs = [s for s in eligible(e, week, cfg["dims"])
                if list(s)[0] not in natural_distinct(survey, week, q) | (natural_distinct(survey, week, direct) if direct else set())]
        if not segs:
            continue
        inc["segment"] = rng.choice(segs)
        start = cur.start + dt.timedelta(days=rng.randrange(0, 5))
        inc["start"], inc["end"] = str(start), str(cur.end)
        key = "small" if label == "too_small" else "size"
        if key not in cfg and label == "too_small":
            raise ValueError(f"{typ} has no too-small variant")
        for attempt in range(6):
            if key in cfg:
                kind, lo, hi = cfg[key]
                val = rng.uniform(lo, hi)
                inc[kind] = round(val, 2)
            if typ == "mix_shift":
                inc["copies"] = 1 if label == "too_small" else rng.choice([1, 2, 3])
            incidents.plant(con, inc)
            o = oracle(e, inc, cur, base)
            if label == "clear" and (o.get("fires") or o.get("distinct")):
                return {"kind": "planted", "question": {"metric": q, "week": str(week)}, "incident": inc, "oracle": o,
                        "expected": {"verdict": "incident", "label": "clear", "type": incidents.DIAGNOSIS[typ],
                                     "segment": inc["segment"], "start": inc["start"], "check_start": True}}
            if label == "too_small" and not o.get("fires") and o.get("z") is not None and abs(o["z"]) < 1.5:
                return {"kind": "planted", "question": {"metric": q, "week": str(week)}, "incident": inc, "oracle": o,
                        "expected": {"verdict": "incident", "label": "too_small", "type": incidents.DIAGNOSIS[typ],
                                     "segment": inc["segment"], "start": inc["start"], "check_start": True,
                                     "accept": ["no_change", "cant_tell"]}}
    raise RuntimeError(f"could not draw a {label} {typ} case")


def quiet_candidates(e: Engine, survey) -> list[dict]:
    out = []
    for week in QUIET_WEEKS:
        w = next(x for x in survey if x["week"] == str(week))
        touched = touched_by_health(survey, week)
        for m, v in w["metrics"].items():
            if m in touched or v["z"] is None or abs(v["z"]) >= 1.5 or v["distinct_segments"]:
                continue
            out.append({"kind": "quiet", "question": {"metric": m, "week": str(week)},
                        "expected": {"verdict": "no_change", "label": "quiet", "accept": ["no_change", "broad"]},
                        "note": f"{METRICS[m].label} {v['rel_change']:+.0%}, {abs(v['z']):.1f}x the typical weekly change; "
                                f"no segment distinct from the rest; no health finding touching it"})
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = duckdb.connect()
    con.execute(f"CREATE TABLE base_sessions AS SELECT * FROM '{(ROOT / 'data' / 'sessions.parquet').as_posix()}'")
    incidents.plant(con, None)
    e = Engine(con)
    survey = json.loads((ROOT / "data" / "natural_weeks.json").read_text(encoding="utf-8"))
    quiet_pool = quiet_candidates(e, survey)
    qrng = random.Random(SEED + 100)
    qrng.shuffle(quiet_pool)
    print(f"quiet candidates: {len(quiet_pool)}")
    cases, used_quiet = [], 0
    for split, offset in (("dev", 0), ("holdout", 1)):
        rng = random.Random(SEED + offset)
        split_cases = []
        for label in ("clear", "too_small"):
            for typ in PLANTED[split][label]:
                c = draw_planted(con, e, survey, rng, typ, label, split)
                o = c["oracle"]
                print(f"  {split} {label:9} {typ:14} {c['question']['metric']:22} wk {c['question']['week']} "
                      f"seg {c['incident']['segment']} start {c['incident']['start']} "
                      f"{'check fires' if o.get('fires') else 'z %+.1f rel %+.0f%%' % (o['z'], 100 * (o.get('relative_to_rest') or 0))}")
                split_cases.append(c)
        split_cases += [dict(n) for n in NATURAL[split]]
        split_cases += quiet_pool[used_quiet: used_quiet + QUIET[split]]
        used_quiet += QUIET[split]
        rng.shuffle(split_cases)
        prefix = "D" if split == "dev" else "H"
        for i, c in enumerate(split_cases, 1):
            c["id"], c["split"] = f"{prefix}{i:02d}", split
            cases.append(c)
    out = {"note": "Frozen before any investigator prompt existed. Rules: data/BENCHMARK_GUIDE.md. "
                   "Generated by scripts/build_benchmark.py with seed %d." % SEED,
           "cases": cases}
    path = ROOT / "data" / "benchmark.json"
    path.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = json.loads((ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    manifest["benchmark"] = {"file": "data/benchmark.json", "cases": len(cases), "sha256": digest}
    (ROOT / "data" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    for split in ("dev", "holdout"):
        cs = [c for c in cases if c["split"] == split]
        print(f"{split}: {len(cs)} cases: " + ", ".join(f"{k} {sum(c['kind'] == k for c in cs)}" for k in ("planted", "natural", "broad", "quiet")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
