"""Tracking-health checks: can the data behind this change be trusted?

Each check compares this week with the week before, for the whole site and per device, OS, browser and
country, and looks for a signature that real customer behaviour can't produce, only a measurement problem:
- purchase events per order rises above 1        (a purchase tag firing twice)
- purchases without a checkout start rise        (a checkout tag that stopped firing)
- an event falls to (almost) zero while sessions continue   (a tag that stopped)
- an event appears from (almost) nothing         (a tag that was missing, or firing only some days)
- revenue per order jumps tenfold or more        (a currency or units bug)
- orders recorded with zero revenue rise         (the revenue value stopped being sent)

The demo data has its own inconsistencies (some purchases never had a checkout event), which is why every check
is relative to the previous week, and needs at least 20 orders (or 100 events) in the segment this
week, so a handful of orders can't trigger it. A segment finding that repeats a site-wide one is dropped.
"""
from .engine import Engine, Window

DIMS = ["device", "os", "browser", "country"]
COLS = ["sessions", "purchase_events", "orders", "revenue", "purchase_no_checkout", "purchase_sessions",
        "zero_revenue_purchases", "view_item", "add_to_cart", "begin_checkout", "add_payment_info", "page_view"]
AGG = """count(*), sum(purchase), sum(transactions), sum(revenue),
         count(*) FILTER (WHERE purchase > 0 AND begin_checkout = 0), count(*) FILTER (WHERE purchase > 0),
         count(*) FILTER (WHERE purchase > 0 AND revenue = 0),
         sum(view_item), sum(add_to_cart), sum(begin_checkout), sum(add_payment_info), sum(page_view)"""
EVENTS = ("view_item", "add_to_cart", "begin_checkout", "add_payment_info", "page_view")


def _rows(e: Engine, dim: str | None, w: Window) -> dict:
    key = dim or "'all'"
    rows = e.con.execute(f"SELECT {key} AS v, {AGG} FROM {e.t} WHERE {w.sql()} GROUP BY 1").fetchall()
    return {r[0]: dict(zip(COLS, [float(x or 0) for x in r[1:]])) for r in rows}


def _ratio(a, b):
    return a / b if b else None


def _checks(was: dict, now: dict) -> list[dict]:
    out = []
    r0, r1 = _ratio(was["purchase_events"], was["orders"]), _ratio(now["purchase_events"], now["orders"])
    if r0 and r1 and now["orders"] >= 20 and r1 >= 1.3 and r1 >= r0 * 1.3:
        out.append({"check": "duplicate_purchase_events", "base": r0, "cur": r1, "unit": "ratio",
                    "detail": f"purchase events per order went from {r0:.2f} to {r1:.2f}"})
    s0 = _ratio(was["purchase_no_checkout"], was["purchase_sessions"])
    s1 = _ratio(now["purchase_no_checkout"], now["purchase_sessions"])
    if s0 is not None and s1 is not None and now["purchase_sessions"] >= 20 and s1 - s0 >= 0.25:
        out.append({"check": "purchases_without_checkout", "base": s0, "cur": s1, "unit": "pct",
                    "detail": f"purchases with no checkout start went from {s0:.0%} to {s1:.0%}"})
    for ev in EVENTS:
        p0, p1 = was[ev] / was["sessions"], now[ev] / now["sessions"]
        if was[ev] >= 30 and p1 <= 0.05 * p0:
            out.append({"check": "event_stopped", "event": ev, "base": p0, "cur": p1, "unit": "ratio",
                        "detail": f"{ev} events per session fell from {p0:.3f} to {p1:.3f} while sessions continued"})
        if now[ev] >= 100 and p0 <= 0.05 * p1:
            out.append({"check": "event_started", "event": ev, "base": p0, "cur": p1, "unit": "ratio",
                        "detail": f"{ev} events per session rose from {p0:.3f} to {p1:.3f}: "
                                  f"the event was barely recorded the week before"})
    a0, a1 = _ratio(was["revenue"], was["orders"]), _ratio(now["revenue"], now["orders"])
    if a0 and a1 and now["orders"] >= 10 and a1 >= 10 * a0:
        out.append({"check": "revenue_per_order_jump", "base": a0, "cur": a1, "unit": "usd",
                    "detail": f"revenue per order went from ${a0:,.0f} to ${a1:,.0f}"})
    z0 = _ratio(was["zero_revenue_purchases"], was["purchase_sessions"])
    z1 = _ratio(now["zero_revenue_purchases"], now["purchase_sessions"])
    if z0 is not None and z1 is not None and now["purchase_sessions"] >= 20 and z1 - z0 >= 0.2:
        out.append({"check": "zero_revenue_orders", "base": z0, "cur": z1, "unit": "pct",
                    "detail": f"purchases recorded with zero revenue went from {z0:.0%} to {z1:.0%}"})
    return out


def check(e: Engine, cur: Window, base: Window, min_sessions: int = 300) -> list[dict]:
    findings, site_keys = [], set()
    for dim in [None] + DIMS:
        b, c = _rows(e, dim, base), _rows(e, dim, cur)
        for v, now in c.items():
            was = b.get(v)
            if not was or now["sessions"] < min_sessions or not was["sessions"]:
                continue
            for f in _checks(was, now):
                key = (f["check"], f.get("event"))
                if dim is None:
                    site_keys.add(key)
                elif key in site_keys:
                    continue
                f["segment"] = {} if dim is None else {dim: v}
                findings.append(f)
    return findings
