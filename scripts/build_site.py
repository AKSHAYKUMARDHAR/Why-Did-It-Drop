"""Build the static benchmark explorer's data from the frozen benchmark and the logged runs (no model calls).

    python -m scripts.build_site

For every case: the question, the shipped answer (model + checks) with its numbers filled in from the engine's
facts, the deterministic baseline's answer, the expected answer and outcome, and the evidence pack as the model
saw it. Writes site/data/cases.json.
"""
import datetime as dt
import json
import pathlib
import sys

import duckdb

from wdid import evidence, incidents
from wdid.engine import Engine, week_of
from wdid.semantic import DIMENSIONS, METRICS

ROOT = pathlib.Path(__file__).resolve().parent.parent
TYPE_LABEL = {"product_break": "Product break", "traffic_loss": "Traffic loss", "mix_shift": "Traffic-mix shift",
              "tracking_issue": "Tracking issue", "basket_change": "Basket change"}
PLANTED_LABEL = {"product_break": "a funnel step stopped for part of a segment", "traffic_loss": "a share of a segment's sessions removed",
                 "mix_shift": "copies of a segment's bounced sessions added", "tracking_dup": "purchase events doubled",
                 "tracking_gap": "checkout-start events removed while purchases continue", "basket_change": "revenue per order cut",
                 "landing_break": "product views stopped for a landing section", "units_bug": "revenue recorded x100"}


def segtext(seg: dict) -> str:
    return " & ".join(f"{DIMENSIONS.get(k, k)} = {v}" for k, v in (seg or {}).items()) or "whole site"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    bench = json.loads((ROOT / "data" / "benchmark.json").read_text(encoding="utf-8"))["cases"]
    runs = {}
    for split in ("dev", "holdout"):
        r = json.loads((ROOT / "eval" / "results" / f"{split}_gemini-3.5-flash-lite.json").read_text(encoding="utf-8"))
        runs.update({c["id"]: c for c in r["cases"]})
    con = duckdb.connect()
    con.execute(f"CREATE TABLE base_sessions AS SELECT * FROM '{(ROOT / 'data' / 'sessions.parquet').as_posix()}'")
    out = []
    for c in bench:
        incidents.plant(con, c.get("incident"))
        e = Engine(con)
        week = dt.date.fromisoformat(c["question"]["week"])
        cur, base = week_of(week), week_of(week - dt.timedelta(days=7))
        pack = evidence.build(e, c["question"]["metric"], cur, base)
        run = runs.get(c["id"], {})
        shipped = (run.get("answers") or {}).get("checked") or {}
        baseline = (run.get("answers") or {}).get("baseline") or {}
        exp, inc = c["expected"], c.get("incident") or {}
        out.append({
            "id": c["id"], "split": c["split"], "kind": c["kind"], "label": exp.get("label"),
            "metric": METRICS[c["question"]["metric"]].label, "week": str(cur), "base": str(base),
            "headline_change": pack["headline"]["rel"], "headline_real": pack["headline"]["real"],
            "planted": {"type": inc.get("type"), "what": PLANTED_LABEL.get(inc.get("type"), ""),
                        "segment": segtext(inc.get("segment")), "start": inc.get("start"),
                        "size": inc.get("fraction") or inc.get("factor")} if inc else None,
            "note": c.get("note"),
            "expected": {"verdict": exp["verdict"], "type": TYPE_LABEL.get(exp.get("type"), ""),
                         "segment": segtext(exp.get("segment")) if exp["verdict"] == "incident" else "",
                         "start": exp.get("start") or "", "accept": exp.get("accept", [])},
            "shipped": {"verdict": shipped.get("verdict"), "type": TYPE_LABEL.get(shipped.get("type"), ""),
                        "segment": segtext(shipped.get("segment")) if shipped.get("verdict") == "incident" else "",
                        "start": shipped.get("start_date") or "", "outcome": shipped.get("outcome"),
                        "text": shipped.get("rendered") or {}, "notes": shipped.get("check_notes") or []},
            "baseline": {"verdict": baseline.get("verdict"), "type": TYPE_LABEL.get(baseline.get("type"), ""),
                         "segment": segtext(baseline.get("segment")) if baseline.get("verdict") == "incident" else "",
                         "start": baseline.get("start_date") or "", "outcome": baseline.get("outcome")},
            "evidence": evidence.render(pack),
        })
        print(f"  {c['id']} {shipped.get('outcome')}")
    summary = {}
    for split in ("dev", "holdout"):
        r = json.loads((ROOT / "eval" / "results" / f"{split}_gemini-3.5-flash-lite.json").read_text(encoding="utf-8"))
        summary[split] = r["summary"]["all"]
    path = ROOT / "site" / "data" / "cases.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cases": out, "summary": summary}, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"{len(out)} cases written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
