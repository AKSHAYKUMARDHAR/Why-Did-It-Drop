"""The evidence pack: what the engine found, as numbered facts the model can cite but never write.

Every number is a fact (F1, F2, ...) with a label, a value and a unit. The model's answer refers to facts by
id ("conversion fell from {F3} to {F4}") and code prints the values, so a figure in an answer is always
one the engine computed. The pack is built in a fixed order: is it real, can the data be trusted, what
drove it, where is it, when did it start.
"""
import datetime as dt
import json
import pathlib

from . import health
from .engine import Engine, Window
from .semantic import DIMENSIONS, METRICS

ROOT = pathlib.Path(__file__).resolve().parent.parent
CALENDAR = json.loads((ROOT / "data" / "calendar.json").read_text(encoding="utf-8")) if (ROOT / "data" / "calendar.json").exists() else []

# Revenue = sessions x conversion x orders per converting session x average order value
DRIVERS = {"revenue": ["sessions", "conversion_rate", "aov"], "orders": ["sessions", "conversion_rate"],
           "revenue_per_session": ["conversion_rate", "aov"]}


def fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "pct":
        return f"{value * 100:.2f}%"
    if unit == "change":
        return f"{value * 100:+.0f}%"
    if unit == "share":
        return f"{value * 100:.0f}%"
    if unit == "pp":
        return f"{value * 100:+.2f} pts"
    if unit == "usd":
        return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "z":
        return f"{value:.1f}x"
    if unit == "date":
        return value.strftime("%d %b %Y") if isinstance(value, (dt.date, dt.datetime)) else str(value)
    return str(value)


class Facts:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, label: str, value, unit: str, **ref) -> str:
        fid = f"F{len(self.items) + 1}"
        self.items.append({"id": fid, "label": label, "value": value, "unit": unit, "text": fmt(value, unit), **ref})
        return fid

    def get(self, fid: str) -> dict | None:
        return next((f for f in self.items if f["id"] == fid), None)

    def lines(self) -> str:
        return "\n".join(f"{f['id']} | {f['label']} | {f['text']}" for f in self.items)


def _unit(metric: str) -> str:
    return {"pct": "pct", "usd": "usd"}.get(METRICS[metric].fmt, "count")


def _segname(seg: dict) -> str:
    return " & ".join(f"{DIMENSIONS.get(k, k)} = {v}" for k, v in seg.items()) or "all traffic"


def build(e: Engine, metric: str, cur: Window, base: Window, focus_segments: int = 3) -> dict:
    facts = Facts()
    pack = {"question": {"metric": metric, "label": METRICS[metric].label, "cur": str(cur), "base": str(base)},
            "facts": facts}
    # 1. the headline and whether it's real
    h = e.compare(metric, cur, base)
    n = e.noise(metric, cur)
    u = _unit(metric)
    pack["headline"] = {
        "base": facts.add(f"{METRICS[metric].label}, {base}", h["base"]["value"], u),
        "cur": facts.add(f"{METRICS[metric].label}, {cur}", h["cur"]["value"], u),
        "change": facts.add(f"{METRICS[metric].label}, change week over week", h["rel"], "change"),
        "typical": facts.add("Typical week-over-week change in the 4 weeks before (absolute)", n["typical_abs_change"], "share"),
        "z": facts.add("This change in units of the typical change", abs(n["z"]) if n["z"] is not None else None, "z"),
    }
    pack["real"] = n["real"]
    # 2. calendar context
    pack["calendar"] = [c for c in CALENDAR if base.start <= dt.date.fromisoformat(c["date"]) <= cur.end]
    # 3. tracking health
    pack["health"] = []
    for f in health.check(e, cur, base):
        f = dict(f)
        f["base_fact"] = facts.add(f"{f['check']} ({_segname(f['segment'])}), {base}", f["base"],
                                   "usd" if f["check"] == "revenue_per_order_jump" else ("pct" if f["check"] != "duplicate_purchase_events" else "count"))
        f["cur_fact"] = facts.add(f"{f['check']} ({_segname(f['segment'])}), {cur}", f["cur"],
                                  "usd" if f["check"] == "revenue_per_order_jump" else ("pct" if f["check"] != "duplicate_purchase_events" else "count"))
        pack["health"].append(f)
    # 4. drivers (for totals that factor into sessions x rates)
    pack["drivers"] = []
    for d in DRIVERS.get(metric, []):
        c = e.compare(d, cur, base)
        nn = e.noise(d, cur)
        pack["drivers"].append({"metric": d, "rel": c["rel"], "real": nn["real"],
                                "fact": facts.add(f"{METRICS[d].label}, change week over week", c["rel"], "change")})
    # 5. the funnel
    pack["funnel"] = []
    for s in e.funnel(cur, base):
        pack["funnel"].append({"step": s["step"], "rel": s["rel"],
                               "base_fact": facts.add(f"{s['label']}, {base}", s["base"], "pct"),
                               "cur_fact": facts.add(f"{s['label']}, {cur}", s["cur"], "pct"),
                               "change_fact": facts.add(f"{s['label']}, change", s["rel"], "change")})
    # 6. where: the metric that moved most (the headline itself, or its largest driver or funnel step)
    focus = _focus(metric, pack)
    pack["focus_metric"] = focus
    ranked = e.rank_dimensions(focus, cur, base)
    pack["dimensions"] = []
    for r in ranked:
        b = e.breakdown(focus, r["dimension"], cur, base)
        segs = []
        for s in b["segments"][:4]:
            seg = {"value": s["value"], "placeholder": s["placeholder"],
                   "share_of_change": facts.add(f"Share of the change in {METRICS[focus].label} from {_segname({r['dimension']: s['value']})}",
                                                s["share_of_change"], "share"),
                   "volume_share": facts.add(f"Share of {'sessions' if METRICS[focus].den == 'count(*)' else 'volume'} from {_segname({r['dimension']: s['value']})}, {base}",
                                             s["share_base"], "share")}
            if METRICS[focus].is_rate:
                seg["base"] = facts.add(f"{METRICS[focus].label}, {_segname({r['dimension']: s['value']})}, {base}", s["base"], _unit(focus))
                seg["cur"] = facts.add(f"{METRICS[focus].label}, {_segname({r['dimension']: s['value']})}, {cur}", s["cur"], _unit(focus))
                seg["mix_effect"] = s["mix_effect"]
                seg["rate_effect"] = s["rate_effect"]
            else:
                seg["base"] = facts.add(f"{METRICS[focus].label}, {_segname({r['dimension']: s['value']})}, {base}", s["base"], _unit(focus))
                seg["cur"] = facts.add(f"{METRICS[focus].label}, {_segname({r['dimension']: s['value']})}, {cur}", s["cur"], _unit(focus))
            seg["share_of_change_value"] = s["share_of_change"]
            segs.append(seg)
        entry = {"dimension": r["dimension"], "surprise": r["surprise"], "segments": segs}
        if METRICS[focus].is_rate and b["delta"]:
            entry["mix_share"] = b["mix_total"] / b["delta"]
            entry["mix_fact"] = facts.add(f"Share of the change in {METRICS[focus].label} explained by traffic mix across {DIMENSIONS[r['dimension']]}",
                                          entry["mix_share"], "share")
        pack["dimensions"].append(entry)
    # 7. two-dimension segments among the top dimensions
    top_dims = [d["dimension"] for d in pack["dimensions"][:focus_segments]]
    pack["pairs"] = []
    for p in e.pairs(focus, top_dims, cur, base, top=4):
        pack["pairs"].append({"segment": p["segment"], "share_of_change_value": p["share_of_change"],
                              "share_of_change": facts.add(f"Share of the change in {METRICS[focus].label} from {_segname(p['segment'])}",
                                                           p["share_of_change"], "share")})
    # 8. when: change points for the top candidate segments
    pack["timing"] = []
    window = Window(base.start - dt.timedelta(days=7), cur.end)
    cands = [{d["dimension"]: d["segments"][0]["value"]} for d in pack["dimensions"][:focus_segments] if d["segments"]]
    for seg in cands + [h_["segment"] for h_ in pack["health"][:2]]:
        cp = e.change_point(focus, window, seg)
        if cp:
            pack["timing"].append({"segment": seg, "start": cp["start"],
                                   "start_fact": facts.add(f"Most likely start of the change in {METRICS[focus].label}, {_segname(seg)}", cp["start"], "date"),
                                   "before_fact": facts.add(f"{METRICS[focus].label}, {_segname(seg)}, daily average before", cp["before"], _unit(focus)),
                                   "after_fact": facts.add(f"{METRICS[focus].label}, {_segname(seg)}, daily average after", cp["after"], _unit(focus))})
    return pack


def _focus(metric: str, pack: dict) -> str:
    """The metric whose change the dimensions should explain."""
    if metric in DRIVERS and pack["drivers"]:
        moved = [d for d in pack["drivers"] if d["rel"] is not None]
        if moved:
            return max(moved, key=lambda d: abs(d["rel"]))["metric"]
    if metric == "conversion_rate":
        steps = [s for s in pack["funnel"] if s["step"] != "conversion_rate" and s["rel"] is not None]
        if steps:
            worst = max(steps, key=lambda s: abs(s["rel"]))
            if abs(worst["rel"]) > 0.02:
                return worst["step"]
    return metric
