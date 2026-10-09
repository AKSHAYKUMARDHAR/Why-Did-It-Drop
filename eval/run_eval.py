"""Run the investigator on the frozen benchmark and score it (rules in data/BENCHMARK_GUIDE.md).

    python -m eval.run_eval --split dev --no-model        # the deterministic baseline only
    python -m eval.run_eval --split dev                   # baseline, model, model + checks (gemini-3.5-flash-lite)
    python -m eval.run_eval --split dev --replay          # re-score cached model answers, no requests
    python -m eval.run_eval --split holdout               # the release run: once

Each case's incident is planted in a fresh copy of the real sessions, the evidence pack is built by the engine,
and every version is scored from the same pack (and the same cached model answer).
"""
import argparse
import collections
import datetime as dt
import json
import pathlib
import sys
import time

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.score import overlap, passes, score, summarise  # noqa: E402
from wdid import evidence, incidents, investigate  # noqa: E402
from wdid.engine import Engine, week_of  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "holdout"], required=True)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-model", action="store_true")
    ap.add_argument("--replay", action="store_true", help="use cached model answers only")
    ap.add_argument("--only", default="")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    cases = [c for c in json.loads((ROOT / "data" / "benchmark.json").read_text(encoding="utf-8"))["cases"]
             if c["split"] == args.split and (not args.only or c["id"] in args.only.split(","))]
    con = duckdb.connect()
    con.execute(f"CREATE TABLE base_sessions AS SELECT * FROM '{(ROOT / 'data' / 'sessions.parquet').as_posix()}'")
    llm = None
    if not args.no_model:
        from wdid.llm import Gemini, LLMError, QuotaExhausted
        llm = Gemini(model=args.model, cache_dir=ROOT / "eval" / "answers", offline=args.replay)
    versions = ["baseline"] + (["model", "checked"] if llm else [])
    results = {v: [] for v in versions}
    detail, usage, unread, stopped = [], collections.Counter(), [], None
    t0 = time.time()
    for c in cases:
        incidents.plant(con, c.get("incident"))
        e = Engine(con)
        week = dt.date.fromisoformat(c["question"]["week"])
        cur, base = week_of(week), week_of(week - dt.timedelta(days=7))
        pack = evidence.build(e, c["question"]["metric"], cur, base)
        answers = {"baseline": investigate.baseline(pack, e, cur)}
        if llm and not stopped:
            try:
                out = llm.ask(c["id"], evidence.render(pack))
                usage.update(out.get("usage") or {})
                m = investigate.normalise(out["answer"])
                answers["model"] = m
                answers["checked"] = investigate.check(m, pack, e, cur, base)
            except QuotaExhausted as ex:
                stopped = str(ex)
            except LLMError as ex:
                print(f"  {c['id']}: model failed: {ex}")
        if llm and "model" not in answers:
            unread.append(c["id"])
            continue
        row = {"id": c["id"], "kind": c["kind"], "label": c["expected"].get("label"), "question": c["question"],
               "expected": c["expected"], "incident": c.get("incident"), "answers": {}}
        for v in versions:
            a = answers[v]
            ov = overlap(con, "sessions", cur.sql(), a.get("segment") or {}, c["expected"].get("segment") or {}) \
                if a.get("verdict") == "incident" and c["expected"].get("segment") else None
            s = score(c, a, ov)
            results[v].append(s)
            row["answers"][v] = {k: a.get(k) for k in ("verdict", "type", "segment", "start_date", "confidence",
                                                       "untraced_numbers", "unknown_facts", "check_notes", "rendered")} | {"outcome": s["outcome"], "checks": s["checks"]}
        detail.append(row)

    model_name = llm.model if llm else "none"
    print(f"\n{args.split}: {len(cases)} cases, model {model_name}"
          + (f", {len(unread)} without a model answer: {', '.join(unread)}" if unread else ""))
    if stopped:
        print(f"  stopped: {stopped}")
    summary = {}
    for group, pick in (("all", lambda r: True), ("planted", lambda r: r["kind"] == "planted"),
                        ("natural+quiet", lambda r: r["kind"] != "planted")):
        print(f"\n {group}")
        summary[group] = {}
        for v in versions:
            rs = [r for r in results[v] if pick(r)]
            if not rs:
                continue
            s = summarise(rs)
            summary[group][v] = s
            print(f"  {v:9} right {s['right']:2}/{s['cases']:2}  wrong {s['wrong']:2}  missed {s['missed']:2}  "
                  f"abstained {s['abstained']:2}  false alarms {s['false_alarms']:2}/{s['quiet_cases']:2} | "
                  f"incidents right {(s['right_rate_incidents'] or 0):.0%}, wrong rate {s['wrong_rate']:.0%}, "
                  f"false-alarm rate {(s['false_alarm_rate'] or 0):.0%}  {'PASS' if passes(s) else 'fail'}")
    if llm:
        traced = [r["answers"]["model"].get("untraced_numbers") or 0 for r in detail if "model" in r["answers"]]
        print(f"\n model answers with numbers typed outside a fact: {sum(1 for t in traced if t)} of {len(traced)} "
              f"(removed by the checks)")
    for v in versions:
        bad = [r for r in detail if r["answers"][v]["outcome"] not in ("right",)]
        if bad and (args.show or v == versions[-1]):
            print(f"\n {v}: not right")
            for r in bad:
                a, ex = r["answers"][v], r["expected"]
                inc = r.get("incident") or {}
                print(f"  {r['id']} {r['kind']:7} {r['label']:9} {r['question']['metric']:22} {r['question']['week']} | "
                      f"expected {ex['verdict']} {ex.get('type', '')} {ex.get('segment', '')} {ex.get('start', '')} "
                      f"{'(' + inc.get('type', '') + ')' if inc else ''}| got {a['outcome']}: {a['verdict']} {a['type']} "
                      f"{a['segment']} {a['start_date']} {a.get('checks') or ''} {('notes: ' + '; '.join(a['check_notes'])) if a.get('check_notes') else ''}")
    print(f"\n tokens: {dict(usage)}   {time.time() - t0:.0f}s")
    out = ROOT / "eval" / "results" / f"{args.split}_{model_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"split": args.split, "model": model_name, "summary": summary, "cases": detail,
                               "unread": unread, "stopped": stopped}, indent=1, default=str) + "\n",
                   encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
