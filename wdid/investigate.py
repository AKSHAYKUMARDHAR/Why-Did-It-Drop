"""Turn an evidence pack into a diagnosis, three ways, so each layer's value is measured on the same evidence.

- baseline: fixed rules, no model (tracking findings first, then the most distinct segment, typed by its profile)
- model:    the model's answer as given
- checked:  the model's answer after code checks (what ships):
    * numbers: any digit typed outside a {F#} placeholder is removed and counted; unknown fact ids are removed
    * a tracking_issue needs a tracking finding, relevant to the question's metric, covering the named segment
    * any other incident must name one of the code's candidate causes, and takes the type its evidence fits
    * broad needs the headline to be beyond normal variation (otherwise it becomes no_change)
    * the start date is replaced by the engine's change point for the named segment when they disagree by
      more than a day
  A claim that fails becomes cant_tell: a wrong cause is the costly error.
"""
import datetime as dt
import re

from .engine import Engine, Window
from .evidence import fits_type, segname
from .semantic import DIMENSIONS, PLACEHOLDERS

# which question metrics a tracking finding can explain
AFFECTS = {"duplicate_purchase_events": {"revenue", "aov", "revenue_per_session"},
           "revenue_per_order_jump": {"revenue", "aov", "revenue_per_session"},
           "zero_revenue_orders": {"revenue", "aov", "revenue_per_session"},
           "purchases_without_checkout": {"checkout_start_rate", "checkout_completion_rate"}}
EVENT_AFFECTS = {"add_to_cart": {"cart_rate", "add_to_cart_rate", "checkout_start_rate"},
                 "begin_checkout": {"checkout_start_rate", "checkout_completion_rate"},
                 "view_item": {"view_item_rate", "cart_rate"}, "add_payment_info": set(), "page_view": set()}
STEPS = ["view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate", "conversion_rate"]
MIN_IMPACT = 0.005  # a candidate must account for at least half a percent of the metric to be called the cause


def relevant(finding: dict, metric: str) -> bool:
    return metric in AFFECTS.get(finding["check"], set()) | EVENT_AFFECTS.get(finding.get("event"), set())


def _share(e: Engine, seg: dict, w: Window) -> float:
    if not seg:
        return 1.0
    cond = " AND ".join(f"{k} = '{v}'" for k, v in seg.items())
    return e.con.execute(f"SELECT avg(CASE WHEN {cond} THEN 1.0 ELSE 0 END) FROM {e.t} WHERE {w.sql()}").fetchone()[0] or 0.0


def baseline(pack: dict, e: Engine, cur: Window) -> dict:
    metric = pack["question"]["metric"]
    findings = [f for f in pack["health"] if relevant(f, metric)]
    if findings:
        f = max(findings, key=lambda f: _share(e, f["segment"], cur))      # the broadest segment
        return {"verdict": "incident", "type": "tracking_issue", "segment": f["segment"],
                "start_date": str(f.get("start") or ""), "confidence": "high", "source": "health"}
    for c in pack["candidates"]:
        if any(v in PLACEHOLDERS for v in c["segment"].values()) or abs(c["impact_value"]) < MIN_IMPACT:
            continue
        return {"verdict": "incident", "type": c["fits"], "segment": c["segment"],
                "start_date": str(c.get("timing", {}).get("start") or ""), "confidence": "medium", "source": "segments"}
    return {"verdict": "broad" if pack["headline"]["real"] else "no_change", "type": "none", "segment": {},
            "start_date": "", "confidence": "medium", "source": "headline"}


FACT = re.compile(r"[{\[](F\d+)[}\]]")   # {F12}, or [F12] copied from the pack's own notation


def normalise(raw: dict) -> dict:
    seg = {}
    for s in raw.get("segment") or []:
        if s.get("dimension") in DIMENSIONS and s.get("value"):
            seg[s["dimension"]] = s["value"]
    return {"verdict": raw.get("verdict"), "type": raw.get("type") if raw.get("verdict") == "incident" else "none",
            "segment": seg if raw.get("verdict") == "incident" else {}, "start_date": raw.get("start_date") or "",
            "confidence": raw.get("confidence"), "headline": raw.get("headline") or "",
            "explanation": raw.get("explanation") or [], "next_step": raw.get("next_step") or ""}


def trace_numbers(text: str, pack: dict) -> tuple[str, int, int]:
    """Replace {F#} with the fact's value and remove digits typed outside placeholders (except inside the names
    of segments the pack mentions). Returns (rendered text, numbers removed, unknown fact ids)."""
    F = pack["facts"]
    names = sorted({str(v) for c in pack["candidates"] for v in c["segment"].values()}
                   | {str(v) for f in pack["health"] for v in f["segment"].values()}, key=len, reverse=True)
    unknown = removed = 0
    out, last = [], 0

    def clean(raw: str) -> str:
        nonlocal removed
        keep = {}
        for i, name in enumerate(n for n in names if any(ch.isdigit() for ch in n)):
            if name in raw:
                keep[f"<<{i}>>"] = name
                raw = raw.replace(name, f"<<{i}>>")
        found = re.findall(r"\$?\d+(?:[.,]\d+)*%?", re.sub(r"<<\d+>>", "", raw))
        removed += len(found)
        raw = re.sub(r"(?<!<)\$?\d+(?:[.,]\d+)*%?(?!>)", "[number removed]", raw)
        for k, v in keep.items():
            raw = raw.replace(k, v)
        return raw
    for m in FACT.finditer(text):
        out.append(clean(text[last:m.start()]))
        f = F.get(m.group(1))
        if f:
            out.append(f["text"])
        else:
            unknown += 1
        last = m.end()
    out.append(clean(text[last:]))
    return "".join(out), removed, unknown


def check(ans: dict, pack: dict, e: Engine, cur: Window, base: Window) -> dict:
    """Apply the code checks to a normalised model answer."""
    a = dict(ans)
    notes = []
    removed = unknown = 0
    rendered = {}
    for key in ("headline", "next_step"):
        rendered[key], r, u = trace_numbers(a.get(key, ""), pack)
        removed, unknown = removed + r, unknown + u
    rendered["explanation"] = []
    for s in a.get("explanation", []):
        t, r, u = trace_numbers(s, pack)
        rendered["explanation"].append(t)
        removed, unknown = removed + r, unknown + u
    a["rendered"] = rendered
    a["untraced_numbers"], a["unknown_facts"] = removed, unknown
    metric = pack["question"]["metric"]

    def downgrade(why):
        notes.append(why)
        a.update(verdict="cant_tell", type="none", segment={}, start_date="")

    if a["verdict"] == "incident":
        seg = a.get("segment") or {}
        if any(v in PLACEHOLDERS for v in seg.values()):
            downgrade("named a placeholder segment")
        elif a["type"] == "tracking_issue":
            fs = [f for f in pack["health"] if relevant(f, metric) and _covers(e, f["segment"], seg, cur)]
            if not fs:
                downgrade("tracking issue claimed without a relevant tracking finding for that segment")
            else:
                if seg and all(not f["segment"] for f in fs):
                    notes.append("the tracking finding is site-wide: segment set to the whole site")
                    a["segment"] = {}
                code_start = fs[0].get("start")
                a["start_date"] = _fix_start(a["start_date"], code_start, notes)
        else:
            cand = next((c for c in pack["candidates"] if c["segment"] == seg), None)
            if cand is None:
                downgrade(f"{a['type']} claimed for {segname(seg)}, which is not among the candidate causes "
                          f"(it did not change distinctly from the rest in a way that made the metric worse)")
            else:
                if a["type"] != cand["fits"]:
                    notes.append(f"type set to {cand['fits']}, the type the segment's evidence fits (model said {a['type']})")
                    a["type"] = cand["fits"]
                a["start_date"] = _fix_start(a["start_date"], cand.get("timing", {}).get("start"), notes)
    elif a["verdict"] == "broad" and not pack["headline"]["real"]:
        notes.append("broad needs a change beyond normal variation: changed to no_change")
        a["verdict"] = "no_change"
    a["check_notes"] = notes
    return a


def _covers(e: Engine, finding_seg: dict, named: dict, cur: Window) -> bool:
    """Does the finding's segment cover most of the named one (or are they the same, or both site-wide)?"""
    if not finding_seg:          # a site-wide finding covers any segment
        return True
    if not named:
        return False
    fa = " AND ".join(f"{k} = '{v}'" for k, v in finding_seg.items())
    na = " AND ".join(f"{k} = '{v}'" for k, v in named.items())
    both, n_named, n_find = e.con.execute(f"""SELECT count(*) FILTER (WHERE ({fa}) AND ({na})),
                                                     count(*) FILTER (WHERE {na}), count(*) FILTER (WHERE {fa})
                                              FROM {e.t} WHERE {cur.sql()}""").fetchone()
    return bool(n_named) and both / n_named >= 0.8 and both / max(n_find, 1) >= 0.5


def _fix_start(model_start: str, code_start, notes: list) -> str:
    if code_start is None:
        return model_start
    try:
        if model_start and abs((dt.date.fromisoformat(model_start) - code_start).days) <= 1:
            return model_start
    except ValueError:
        pass
    notes.append(f"start date set to the engine's change point ({code_start})")
    return str(code_start)
