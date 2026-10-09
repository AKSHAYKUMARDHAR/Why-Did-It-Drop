"""Survey the natural data, week by week, before any incident is planted or any prompt written.

    python -m scripts.survey_weeks

For every week from 9 Nov 2020 to 25 Jan 2021 (compared with the week before) and every question metric:
the headline change and whether it's beyond normal variation, the tracking-health findings, and every
eligible segment that changed distinctly from the rest of the site (|z| >= 3 and >= 15% apart). Writes data/natural_weeks.json.
The benchmark uses it to (a) place planted incidents only where the metric has no natural distinct segment,
and (b) pick the natural cases, whose expected answers are written by hand from this survey.
"""
import datetime as dt
import json
import pathlib
import sys

import duckdb

from wdid import health
from wdid.engine import Engine, week_of

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUESTION_METRICS = ["conversion_rate", "revenue", "sessions", "orders", "aov", "checkout_completion_rate",
                    "checkout_start_rate", "cart_rate", "view_item_rate", "add_to_cart_rate"]
WEEKS = [dt.date(2020, 11, 9) + dt.timedelta(days=7 * k) for k in range(12)]
MIN_SHARE = 0.05      # a segment needs at least 5% of the week's sessions to be eligible


def eligible_segments(e: Engine, cur) -> list[dict]:
    out = []
    for dim in ("device", "os", "browser", "country", "medium", "source", "landing", "user_type"):
        rows = e.con.execute(f"""SELECT {dim}, count(*) * 1.0 / sum(count(*)) OVER () AS share
                                 FROM {e.t} WHERE {cur.sql()} GROUP BY 1""").fetchall()
        out += [{dim: v} for v, share in rows if share >= MIN_SHARE and v not in ("<Other>", "(not set)", "(data deleted)", "", None)]
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    con = duckdb.connect()
    con.execute(f"CREATE TABLE sessions AS SELECT * FROM '{(ROOT / 'data' / 'sessions.parquet').as_posix()}'")
    e = Engine(con)
    weeks = []
    for start in WEEKS:
        cur, base = week_of(start), week_of(start - dt.timedelta(days=7))
        segs = eligible_segments(e, cur)
        entry = {"week": str(start), "health": [{k: v for k, v in f.items() if k != "detail"} | {"detail": f["detail"]}
                                                for f in health.check(e, cur, base)], "metrics": {}}
        for m in QUESTION_METRICS:
            n = e.noise(m, cur)
            distinct = []
            for s in segs:
                r = e.versus_rest(m, s, cur, base)
                if r["distinct"]:
                    distinct.append({"segment": s, "z": round(r["z"], 1), "seg_rel": r["seg_rel"], "rest_rel": r["rest_rel"],
                                     "relative_to_rest": r["relative_to_rest"]})
            entry["metrics"][m] = {"rel_change": n["rel_change"], "band": n["typical_abs_change"], "z": n["z"],
                                   "real": n["real"], "distinct_segments": sorted(distinct, key=lambda d: -abs(d["z"]))}
        weeks.append(entry)
        quiet = [m for m, v in entry["metrics"].items() if not v["distinct_segments"]]
        print(f"{start}  health {len(entry['health'])}  metrics with no distinct segment: {len(quiet)}/{len(QUESTION_METRICS)}")
    (ROOT / "data" / "natural_weeks.json").write_text(json.dumps(weeks, indent=1, default=str) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
