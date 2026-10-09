"""Score one diagnosis against a benchmark case (rules in data/BENCHMARK_GUIDE.md).

Outcomes: right, wrong (the costly error: a confident wrong cause), missed, abstained, false_alarm.
Segments are matched by the sessions they cover in the question week, not by their labels: "os = iOS &
browser = Safari" matches a planted "os = iOS" (89% of its sessions), while "device = desktop" does not match
"os = Web" (46% overlap in this obfuscated data).
"""
import datetime as dt

VERDICTS = ["incident", "broad", "no_change", "cant_tell"]
TYPES = ["product_break", "traffic_loss", "mix_shift", "tracking_issue", "basket_change"]


def _cond(seg: dict) -> str:
    if not seg:
        return "TRUE"
    return " AND ".join(f"{k} = '{str(v).replace(chr(39), chr(39) * 2)}'" for k, v in seg.items())


def overlap(con, table: str, week_sql: str, answer: dict, planted: dict) -> dict:
    a, p = _cond(answer), _cond(planted)
    row = con.execute(f"""SELECT count(*) FILTER (WHERE {a}), count(*) FILTER (WHERE {p}),
                                 count(*) FILTER (WHERE ({a}) AND ({p})), count(*) FILTER (WHERE ({a}) OR ({p}))
                          FROM {table} WHERE {week_sql}""").fetchone()
    na, np_, both, either = (float(x or 0) for x in row)
    return {"answer": na, "planted": np_, "both": both, "jaccard": both / either if either else 0.0,
            "inside": both / na if na else 0.0, "coverage": both / np_ if np_ else 0.0}


def segment_match(answer: dict, planted: dict, ov: dict | None) -> bool:
    if not planted:                      # a site-wide case needs a site-wide answer
        return not answer
    if not answer:
        return False
    if ov is None:                       # no data to hand: exact dimension-value match on shared dimensions
        shared = set(answer) & set(planted)
        return bool(shared) and all(str(answer[k]).lower() == str(planted[k]).lower() for k in shared)
    return ov["jaccard"] >= 0.8 or (ov["inside"] >= 0.9 and ov["coverage"] >= 0.5)


def score(case: dict, ans: dict, ov: dict | None = None) -> dict:
    exp = case["expected"]
    v = ans.get("verdict")
    out = {"id": case["id"], "kind": case["kind"], "label": exp.get("label"), "verdict": v, "checks": {}}
    if exp["verdict"] in ("no_change", "broad"):
        if v == "incident":
            out["outcome"] = "false_alarm"
        elif v == "cant_tell":
            out["outcome"] = "abstained"
        elif v in exp.get("accept", [exp["verdict"]]):
            out["outcome"] = "right"
        else:
            out["outcome"] = "missed"         # "no_change" on a broad case
        return out
    # an incident (planted or natural)
    if v == "cant_tell":
        out["outcome"] = "right" if exp.get("label") == "too_small" else "abstained"
        return out
    if v != "incident":
        out["outcome"] = "right" if v in exp.get("accept", []) else "missed"
        return out
    type_ok = ans.get("type") == exp["type"]
    seg_ok = segment_match(ans.get("segment") or {}, exp["segment"], ov)
    date_ok = True
    if exp.get("check_start"):
        try:
            date_ok = abs((dt.date.fromisoformat(str(ans.get("start_date"))) - dt.date.fromisoformat(exp["start"])).days) <= 1
        except ValueError:
            date_ok = False
    out["checks"] = {"type": type_ok, "segment": seg_ok, "start": date_ok}
    out["outcome"] = "right" if type_ok and seg_ok and date_ok else "wrong"
    return out


def summarise(results: list[dict]) -> dict:
    inc = [r for r in results if r["label"] in ("clear", "too_small")]
    quiet = [r for r in results if r["label"] in ("quiet", "broad")]
    n = len(results) or 1

    def cnt(rs, o):
        return sum(r["outcome"] == o for r in rs)
    s = {"cases": len(results), "incident_cases": len(inc), "quiet_cases": len(quiet),
         "right": cnt(results, "right"), "wrong": cnt(results, "wrong"), "missed": cnt(results, "missed"),
         "abstained": cnt(results, "abstained"), "false_alarms": cnt(results, "false_alarm")}
    s["wrong_rate"] = s["wrong"] / n
    s["right_rate_incidents"] = cnt(inc, "right") / len(inc) if inc else None
    s["false_alarm_rate"] = s["false_alarms"] / len(quiet) if quiet else None
    return s


GATE = {"wrong_rate": 0.05, "right_rate_incidents": 0.75, "false_alarm_rate": 0.10}


def passes(s: dict) -> bool:
    return (s["wrong_rate"] <= GATE["wrong_rate"]
            and (s["right_rate_incidents"] or 0) >= GATE["right_rate_incidents"]
            and (s["false_alarm_rate"] or 0) <= GATE["false_alarm_rate"])
