"""The evidence pack: what the engine found, as numbered facts the model can cite but never write.

Every number is a fact (F1, F2, ...) with a label, a value and a unit. The model's answer refers to facts by id
("conversion fell from {F3} to {F4}") and code prints the values, so a figure in an answer is always one the
engine computed. The pack follows a fixed order: is the change real, can the data be trusted, what drove it,
which segments changed differently from the rest of the site, and when it started.
"""
import datetime as dt
import json
import pathlib

from . import health
from .engine import Engine, Window
from .semantic import DIMENSIONS, FUNNEL, METRICS, PLACEHOLDERS

ROOT = pathlib.Path(__file__).resolve().parent.parent
CALENDAR = json.loads((ROOT / "data" / "calendar.json").read_text(encoding="utf-8"))

DRIVERS = {"revenue": ["sessions", "conversion_rate", "aov"], "orders": ["sessions", "conversion_rate"],
           "revenue_per_session": ["conversion_rate", "aov"]}
# the metrics that describe a segment's incident profile
PROFILE = ["sessions", "conversion_rate", "view_item_rate", "cart_rate", "checkout_start_rate",
           "checkout_completion_rate", "aov"]
MIN_SHARE = 0.05
# What can move each question metric: a candidate's failing metric must be the question's metric or on its path.
# Traffic (sessions) explains totals; for rates over sessions only a mix shift can (more low-intent visitors).
PATH = {
    "revenue": {"sessions", "conversion_rate", "view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate", "aov"},
    "orders": {"sessions", "conversion_rate", "view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate"},
    "sessions": {"sessions"},
    "users": {"sessions"},
    "conversion_rate": {"mix:sessions", "conversion_rate", "view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate"},
    "revenue_per_session": {"mix:sessions", "conversion_rate", "view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate", "aov"},
    "view_item_rate": {"mix:sessions", "view_item_rate"},
    "add_to_cart_rate": {"mix:sessions", "view_item_rate", "cart_rate"},
    "cart_rate": {"cart_rate"},
    "checkout_start_rate": {"checkout_start_rate"},
    "checkout_completion_rate": {"checkout_completion_rate"},
    "aov": {"aov"},
}


def fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "pct":
        return f"{value * 100:.2f}%"
    if unit == "change":
        return f"{value * 100:+.0f}%"
    if unit == "share":
        return f"{value * 100:.0f}%"
    if unit == "usd":
        return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"
    if unit == "count":
        return f"{value:,.0f}"
    if unit == "x":
        return f"{value:.1f}"
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "date":
        return value.strftime("%a %d %b %Y") if isinstance(value, (dt.date, dt.datetime)) else str(value)
    return str(value)


class Facts:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, label: str, value, unit: str) -> str:
        fid = f"F{len(self.items) + 1}"
        self.items.append({"id": fid, "label": label, "value": value, "unit": unit, "text": fmt(value, unit)})
        return fid

    def get(self, fid: str) -> dict | None:
        i = int(fid[1:]) - 1 if fid[1:].isdigit() else -1
        return self.items[i] if 0 <= i < len(self.items) else None


def unit_of(metric: str) -> str:
    return {"pct": "pct", "usd": "usd"}.get(METRICS[metric].fmt, "count")


def segname(seg: dict) -> str:
    return " & ".join(f"{DIMENSIONS.get(k, k)} = {v}" for k, v in seg.items()) or "the whole site"


def eligible_segments(e: Engine, cur: Window, base: Window | None = None) -> list[dict]:
    """Segments with at least 5% of sessions in this week or (if given) last week: a segment that lost most of
    its traffic must still be nameable."""
    out = []
    for dim in DIMENSIONS:
        seen = set()
        for w in [cur] + ([base] if base else []):
            rows = e.con.execute(f"""SELECT {dim}, count(*) * 1.0 / sum(count(*)) OVER () FROM {e.t}
                                     WHERE {w.sql()} GROUP BY 1""").fetchall()
            seen |= {v for v, share in rows if share >= MIN_SHARE and v not in PLACEHOLDERS}
        out += [{dim: v} for v in sorted(seen, key=str)]
    return out


def focus_metric(metric: str, drivers: list[dict], funnel: list[dict]) -> str:
    """The metric whose change the segments should explain: the question's own, unless a driver or funnel step
    moved much more."""
    if metric in DRIVERS and drivers:
        moved = [d for d in drivers if d["rel"] is not None]
        if moved:
            return max(moved, key=lambda d: abs(d["rel"]))["metric"]
    if metric == "conversion_rate":
        steps = [s for s in funnel if s["step"] != "conversion_rate" and s["rel"] is not None]
        if steps:
            worst = max(steps, key=lambda s: abs(s["rel"]))
            if abs(worst["rel"]) > 0.05:
                return worst["step"]
    return metric


def build(e: Engine, metric: str, cur: Window, base: Window, top: int = 6) -> dict:
    F = Facts()
    q = {"metric": metric, "label": METRICS[metric].label, "cur": str(cur), "base": str(base)}
    pack = {"question": q, "facts": F}
    u = unit_of(metric)

    # 1. the headline, and whether it is beyond normal variation
    h, n = e.compare(metric, cur, base), e.noise(metric, cur)
    pack["headline"] = {
        "base": F.add(f"{q['label']}, week of {base.start:%d %b}", h["base"]["value"], u),
        "cur": F.add(f"{q['label']}, week of {cur.start:%d %b}", h["cur"]["value"], u),
        "change": F.add(f"{q['label']}, change week over week", h["rel"], "change"),
        "typical": F.add("Typical week-over-week change in the previous weeks (median, absolute)", n["typical_abs_change"], "share"),
        "z": F.add("This week's change as a multiple of the typical change", abs(n["z"]) if n["z"] is not None else None, "x"),
        "real": n["real"], "rel": h["rel"], "z_value": n["z"],
    }
    # 2. calendar
    pack["calendar"] = [c for c in CALENDAR if base.start <= dt.date.fromisoformat(c["date"]) <= cur.end]
    # 3. tracking health
    pack["health"] = []
    for f in health.check(e, cur, base):
        unit = {"pct": "pct", "usd": "usd"}.get(f["unit"], "ratio")
        what = f["check"].replace("_", " ") + (f" ({f['event']})" if f.get("event") else "")
        pack["health"].append({**f, "base_fact": F.add(f"{what}, {segname(f['segment'])}, week of {base.start:%d %b}", f["base"], unit),
                               "cur_fact": F.add(f"{what}, {segname(f['segment'])}, week of {cur.start:%d %b}", f["cur"], unit)})
    # 4. drivers and funnel
    pack["drivers"] = []
    for d in DRIVERS.get(metric, []):
        c = e.compare(d, cur, base)
        pack["drivers"].append({"metric": d, "rel": c["rel"], "real": e.noise(d, cur)["real"],
                                "fact": F.add(f"{METRICS[d].label}, change week over week", c["rel"], "change")})
    pack["funnel"] = []
    for s in e.funnel(cur, base):
        pack["funnel"].append({"step": s["step"], "rel": s["rel"],
                               "base_fact": F.add(f"{s['label']}, week of {base.start:%d %b}", s["base"], "pct"),
                               "cur_fact": F.add(f"{s['label']}, week of {cur.start:%d %b}", s["cur"], "pct"),
                               "change_fact": F.add(f"{s['label']}, change", s["rel"], "change")})
    pack["focus_metric"] = metric
    # 5. candidate causes: segments that moved differently from the rest of the site in a way that fits an
    #    incident type, ranked by how much of the question metric's change they account for
    window = Window(base.start, cur.end)
    eligible = {(list(sg)[0], list(sg.values())[0]) for sg in eligible_segments(e, cur, base)}
    profiles: dict = {}
    for m in dict.fromkeys(PROFILE + [metric]):
        for dim in DIMENSIONS:
            for v, r in e.scan(m, dim, cur).items():
                if (dim, v) in eligible:
                    profiles.setdefault((dim, v), []).append({"metric": m, "z_value": r["z"], "distinct": r["distinct"],
                                                              "seg_rel": r["seg_rel"], "rest_rel": r["rest_rel"],
                                                              "usual": r["typical_did"], "unusual": r["unusual"]})
    cands = []
    for (dim, v), prof in profiles.items():
        fit = fits_type(prof)
        if not fit:
            continue
        seg = {dim: v}
        imp = e.impact(metric, seg, cur, base)
        if imp >= 0:
            continue    # it did not make the question's metric worse, so it can't explain a failure in it
        key = key_metric(fit, prof)
        path = PATH.get(metric, set())
        if not (key in path and fit != "mix_shift" or (fit == "mix_shift" and ("mix:sessions" in path or "sessions" in path))):
            continue    # its failure is not on the question metric's path
        if fit == "traffic_loss" and "sessions" not in path:
            continue
        severity = abs(next((x["seg_rel"] for x in prof if x["metric"] == key), 0.0) or 0.0)
        cands.append({"segment": seg, "profile": prof, "fits": fit, "impact_value": imp, "key_metric": key,
                      "severity": severity, "score": abs(imp) * severity})
    cands.sort(key=lambda c: -c["score"])
    cands = prefer_specific(e, cands, cur)
    pack["candidates"] = []
    for c in cands[:top]:
        seg = c["segment"]
        c["impact"] = F.add(f"Share of the change in {q['label']} accounted for by {segname(seg)} behaving differently from the rest",
                            c["impact_value"], "change")
        share = e.con.execute(f"SELECT avg(CASE WHEN {list(seg)[0]} = ? THEN 1.0 ELSE 0 END) FROM {e.t} WHERE {cur.sql()}",
                              [list(seg.values())[0]]).fetchone()[0]
        c["share"] = F.add(f"{segname(seg)}, share of the week's sessions", share, "share")
        c["distinct"] = True
        c["key_fact"] = F.add(f"{METRICS[c['key_metric']].label}, {segname(seg)}, change", c["severity"] * (1 if c["fits"] == "mix_shift" else -1), "change")
        for p_ in c["profile"]:
            if p_["distinct"] or p_["metric"] == metric:
                p_["seg_change"] = F.add(f"{METRICS[p_['metric']].label}, {segname(seg)}, change", p_["seg_rel"], "change")
                p_["rest_change"] = F.add(f"{METRICS[p_['metric']].label}, rest of the site, change", p_["rest_rel"], "change")
                p_["z"] = F.add(f"{segname(seg)} versus the rest, {METRICS[p_['metric']].label}: strength of the difference (z)",
                                abs(p_["z_value"]), "x")
        strongest = max((p_ for p_ in c["profile"] if p_["distinct"]), key=lambda p_: abs(p_["z_value"]))
        cp = e.change_point_relative(strongest["metric"], seg, window, earliest=cur.start)
        if cp:
            c["timing"] = {"metric": strongest["metric"], "start": cp["start"],
                           "start_fact": F.add(f"Most likely first day {segname(seg)} diverged from the rest "
                                               f"({METRICS[strongest['metric']].label})", cp["start"], "date")}
        pack["candidates"].append(c)
    # 6. timing of a site-wide change and of the health findings
    pack["site_timing"] = None
    cp = e.change_point(metric, window)
    if cp:
        pack["site_timing"] = {"start": cp["start"], "start_fact": F.add("Most likely first day of the change, whole site", cp["start"], "date")}
    for f in pack["health"]:
        cp = health.start_of(e, f, Window(base.start - dt.timedelta(days=7), cur.end))
        if cp:
            f["start"] = cp
            f["start_fact"] = F.add(f"First day of the {f['check'].replace('_', ' ')} signature, {segname(f['segment'])}", cp, "date")
    return pack


STEPS = ["view_item_rate", "cart_rate", "checkout_start_rate", "checkout_completion_rate", "conversion_rate"]


def prefer_specific(e: Engine, cands: list[dict], cur: Window) -> list[dict]:
    """When a narrower candidate sits inside a broader one (one referral source inside all referral traffic) and
    accounts for most of its effect, put the narrower one first: it is the more specific explanation."""
    out = list(cands)
    for i, a in enumerate(list(out[:4])):
        for b in out[i + 1:]:
            if b["segment"].keys() == a["segment"].keys() or b["impact_value"] * a["impact_value"] <= 0:
                continue
            if abs(b["impact_value"]) < 0.7 * abs(a["impact_value"]):
                continue
            ca = " AND ".join(f"{k} = '{v}'" for k, v in a["segment"].items())
            cb = " AND ".join(f"{k} = '{v}'" for k, v in b["segment"].items())
            both, nb = e.con.execute(f"""SELECT count(*) FILTER (WHERE ({ca}) AND ({cb})), count(*) FILTER (WHERE {cb})
                                         FROM {e.t} WHERE {cur.sql()}""").fetchone()
            if nb and both / nb >= 0.9:
                out.remove(b)
                out.insert(out.index(a), b)
                break
    return out


def key_metric(fit: str, profile: list[dict]) -> str:
    """The metric that carries a candidate's failure: sessions for traffic, order value for baskets, otherwise
    the funnel step that fell most distinctly."""
    if fit in ("traffic_loss", "mix_shift"):
        return "sessions"
    if fit == "basket_change":
        return "aov"
    downs = [p for p in profile if p["distinct"] and p["z_value"] < 0 and p["metric"] in STEPS]
    return max(downs, key=lambda p: abs(p["z_value"]))["metric"] if downs else "conversion_rate"


def fits_type(profile: list[dict]) -> str | None:
    """The incident type a segment's distinct movements fit, if any. Every incident type is a failure, so a rise
    in conversion or order value fits none, and a segment that only grew less than the rest (the mirror image of
    another segment's growth) fits none either: the metric must have worsened in absolute terms."""
    p = {x["metric"]: x for x in profile}

    def down(m, by=0.10):
        """distinct from the rest AND worse in absolute terms (not just growing less than everyone else)"""
        return m in p and p[m]["distinct"] and p[m]["z_value"] < 0 and (p[m].get("seg_rel") is None or p[m]["seg_rel"] <= -by)

    def up(m, by=0.15):
        return m in p and p[m]["distinct"] and p[m]["z_value"] > 0 and (p[m].get("seg_rel") is None or p[m]["seg_rel"] >= by)
    if up("sessions") and (down("conversion_rate") or down("view_item_rate")):
        return "mix_shift"
    if down("sessions", by=0.15):
        return "traffic_loss"
    if down("aov") and not down("conversion_rate"):
        return "basket_change"
    if any(down(m) for m in STEPS):
        return "product_break"
    if down("aov"):
        return "basket_change"
    return None


def render(pack: dict) -> str:
    """The pack as text for the model: sections in a fixed order, every number as [F#] value."""
    F = pack["facts"]

    def t(fid):
        f = F.get(fid)
        return f"[{fid}] {f['text']}" if f else "n/a"
    q, h = pack["question"], pack["headline"]
    L = [f"QUESTION: What changed in {q['label']} in the week of {q['cur']}, compared with the week of {q['base']}, and why?", ""]
    L.append("1. HEADLINE")
    L.append(f"- {q['label']}: {t(h['base'])} -> {t(h['cur'])}, change {t(h['change'])}")
    L.append(f"- Typical weekly change before: {t(h['typical'])}; this change is {t(h['z'])} times that "
             f"({'BEYOND' if h['real'] else 'within'} normal variation)")
    L.append("2. CALENDAR: " + (", ".join(f"{c['event']} ({c['date']})" for c in pack["calendar"]) or "no listed events"))
    from .investigate import relevant
    rel = [f for f in pack["health"] if relevant(f, pack["question"]["metric"])]
    L.insert(1, f"SUMMARY: relevant tracking findings: {len(rel)}; candidate causes: {len(pack['candidates'])}; "
                f"headline {'BEYOND' if h['real'] else 'within'} normal variation")
    L.append("3. TRACKING HEALTH: " + ("no findings" if not pack["health"] else ""))
    for f in pack["health"]:
        L.append(f"- {f['check']}{' (' + f['event'] + ')' if f.get('event') else ''} in {segname(f['segment'])}: "
                 f"{t(f['base_fact'])} -> {t(f['cur_fact'])}" + (f", starting {t(f['start_fact'])}" if f.get("start_fact") else "")
                 + f"; affects this metric: {'yes' if relevant(f, pack['question']['metric']) else 'no'}")
    if pack["drivers"]:
        L.append("4. DRIVERS")
        for d in pack["drivers"]:
            L.append(f"- {METRICS[d['metric']].label}: {t(d['fact'])} ({'beyond' if d['real'] else 'within'} normal variation)")
    L.append("5. FUNNEL (site)")
    for s in pack["funnel"]:
        L.append(f"- {METRICS[s['step']].label}: {t(s['base_fact'])} -> {t(s['cur_fact'])} ({t(s['change_fact'])})")
    L.append(f"6. CANDIDATE CAUSES: segments that changed differently from the rest of the site in a way that fits an "
             f"incident type and made {q['label']} worse (distinct = z of 3 or more, at least 15% apart from the rest, and at least "
             f"twice the segment's usual divergence), strongest first")
    if not pack["candidates"]:
        L.append("- none")
    for c in pack["candidates"]:
        L.append(f"- {segname(c['segment'])} ({t(c['share'])} of sessions), fits {c['fits']}: accounts for {t(c['impact'])} of the change "
                 f"in {q['label']}; its {METRICS[c['key_metric']].label.lower()} changed {t(c['key_fact'])}")
        for p in c["profile"]:
            if "seg_change" in p:
                L.append(f"    · {METRICS[p['metric']].label}: segment {t(p['seg_change'])}, rest {t(p['rest_change'])}, z {t(p['z'])}"
                         f"{'  DISTINCT' if p['distinct'] else ''}")
        if c.get("timing"):
            L.append(f"    · diverged from the rest from {t(c['timing']['start_fact'])}")
    if pack["site_timing"]:
        L.append(f"7. SITE-WIDE CHANGE POINT: {t(pack['site_timing']['start_fact'])}")
    return "\n".join(L)
