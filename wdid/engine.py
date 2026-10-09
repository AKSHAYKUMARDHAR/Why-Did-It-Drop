"""The analysis engine: every number the investigator shows is computed here, in DuckDB.

A question is a metric and a week (`cur`), compared with the week before (`base`). The engine answers:
- is the change real?        the week-over-week change against the previous weeks' normal variation
- where is it?               the funnel step by step; each dimension split into mix and rate effects
- when did it start?         a change point in the daily series of a segment
- can the data be trusted?   tracking-health checks (see wdid/health.py)
"""
import datetime as dt
import math
from dataclasses import dataclass

import duckdb

from .semantic import DIMENSIONS, FUNNEL, METRICS, PLACEHOLDERS

D = dt.date


@dataclass(frozen=True)
class Window:
    start: D
    end: D          # inclusive

    def sql(self) -> str:
        return f"date BETWEEN DATE '{self.start}' AND DATE '{self.end}'"

    def shift(self, days: int) -> "Window":
        return Window(self.start + dt.timedelta(days=days), self.end + dt.timedelta(days=days))

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __str__(self) -> str:
        return f"{self.start:%d %b} - {self.end:%d %b %Y}"


def week_of(start: D) -> Window:
    return Window(start, start + dt.timedelta(days=6))


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def seg_sql(segment: dict | None) -> str:
    if not segment:
        return "TRUE"
    return " AND ".join(f"{k} = {_q(v)}" if v is not None else f"{k} IS NULL" for k, v in segment.items())


class Engine:
    def __init__(self, con: duckdb.DuckDBPyConnection, table: str = "sessions"):
        self.con, self.t = con, table

    # ---- basic values -------------------------------------------------------------------------
    def value(self, metric: str, w: Window, segment: dict | None = None) -> dict:
        m = METRICS[metric]
        cols = f"{m.num} AS num" + (f", {m.den} AS den" if m.den else "")
        row = self.con.execute(f"SELECT {cols} FROM {self.t} WHERE {w.sql()} AND {seg_sql(segment)}").fetchone()
        num = float(row[0] or 0)
        den = float(row[1] or 0) if m.den else None
        val = (num / den if den else None) if m.den else num
        return {"num": num, "den": den, "value": val}

    def compare(self, metric: str, cur: Window, base: Window, segment: dict | None = None) -> dict:
        a, b = self.value(metric, cur, segment), self.value(metric, base, segment)
        delta = None if a["value"] is None or b["value"] is None else a["value"] - b["value"]
        rel = delta / b["value"] if delta is not None and b["value"] else None
        return {"metric": metric, "segment": segment or {}, "cur": a, "base": b, "delta": delta, "rel": rel}

    # ---- is it real? ----------------------------------------------------------------------------
    def first_date(self) -> D:
        if not hasattr(self, "_first"):
            self._first = self.con.execute(f"SELECT min(date) FROM {self.t}").fetchone()[0]
        return self._first

    def noise(self, metric: str, cur: Window, weeks: int = 4, segment: dict | None = None) -> dict:
        """The week-over-week change against normal variation over the `weeks` weeks before `cur`.

        The band is the larger of (a) the median absolute week-over-week change of the previous weeks and
        (b) sampling noise (binomial for rates, Poisson for counts) at this week's volume. Weeks before the
        data starts are left out.
        """
        m = METRICS[metric]
        ks = [k for k in range(weeks + 1, -1, -1) if cur.shift(-7 * k).start >= self.first_date()]
        series = [self.value(metric, cur.shift(-7 * k), segment) for k in ks]
        vals = [s["value"] for s in series]
        hist = vals[:-1]
        changes = [(b - a) / a for a, b in zip(hist, hist[1:]) if a and b is not None]
        # median absolute change: one extreme week (Christmas) shouldn't set the band on its own
        spread = sorted(abs(c) for c in changes)[len(changes) // 2] if changes else None
        last, now = vals[-2], vals[-1]
        rel = (now - last) / last if last and now is not None else None
        cur_v = series[-1]
        if m.is_rate and cur_v["den"] and now is not None and m.fmt == "pct":
            p = max(min(now, 1 - 1e-9), 1e-9)
            sampling = math.sqrt(p * (1 - p) / cur_v["den"]) / p * math.sqrt(2)
        elif not m.is_rate and m.name in ("sessions", "users", "orders") and now:
            sampling = math.sqrt(2 / now)
        else:   # revenue, aov, revenue per session: no closed form, rely on the history
            sampling = 0.0
        band = max(spread or 0.0, sampling)
        z = rel / band if rel is not None and band and len(changes) >= 2 else None
        return {"metric": metric, "weekly_values": vals, "weeks": [str(cur.shift(-7 * k)) for k in ks],
                "rel_change": rel, "typical_abs_change": band, "z": z, "history_weeks": len(changes),
                "real": bool(z is not None and abs(z) >= 2.0)}

    def versus_rest(self, metric: str, segment: dict, cur: Window, base: Window) -> dict:
        """Did this segment change differently from the rest of the site? (a difference-in-differences test)

        Rates: change in log-odds in the segment minus change in log-odds in the rest, with its standard error.
        Counts: change in log volume in the segment minus in the rest (Poisson). Basket metrics (revenue per
        order): change in log mean revenue per order, with standard errors from the spread of order values.
        A seasonal change that hits every segment alike gives a difference near zero. "Distinct" needs both
        |z| >= 3 and a segment change at least 15% different from the rest's (with tens of thousands of
        sessions, tiny shifts are statistically significant without mattering).
        """
        m = METRICS[metric]
        inside, outside = seg_sql(segment), f"NOT ({seg_sql(segment)})"

        def cells(cond):
            out = []
            for w in (base, cur):
                if m.is_rate and m.fmt == "pct":
                    r = self.con.execute(f"SELECT {m.num}, {m.den} FROM {self.t} WHERE {w.sql()} AND {cond}").fetchone()
                    out.append((float(r[0] or 0), float(r[1] or 0)))
                elif metric in ("aov", "revenue_per_session", "revenue"):
                    unit = "transactions" if metric in ("aov", "revenue") else "1"
                    r = self.con.execute(f"""SELECT sum(revenue), sum({unit}), stddev_samp(revenue / nullif({unit}, 0))
                                              FROM {self.t} WHERE {w.sql()} AND {cond}
                                              {"AND transactions > 0" if metric in ("aov", "revenue") else ""}""").fetchone()
                    out.append((float(r[0] or 0), float(r[1] or 0), float(r[2] or 0)))
                else:
                    r = self.con.execute(f"SELECT {m.num} FROM {self.t} WHERE {w.sql()} AND {cond}").fetchone()
                    out.append((float(r[0] or 0),))
            return out
        a, b = cells(inside), cells(outside)
        try:
            if m.is_rate and m.fmt == "pct":
                def lo(x, n):
                    x, n = x + 0.5, n + 1.0          # continuity correction
                    return math.log(x / (n - x)), 1 / x + 1 / (n - x)
                (s0, v_s0), (s1, v_s1) = lo(*a[0]), lo(*a[1])
                (r0, v_r0), (r1, v_r1) = lo(*b[0]), lo(*b[1])
                seg_rel = (a[1][0] / a[1][1]) / (a[0][0] / a[0][1]) - 1 if a[0][0] and a[1][1] and a[0][1] else None
                rest_rel = (b[1][0] / b[1][1]) / (b[0][0] / b[0][1]) - 1 if b[0][0] and b[1][1] and b[0][1] else None
            elif metric in ("aov", "revenue_per_session"):
                def lm(rev, n, sd):
                    mean = rev / n
                    return math.log(mean), (sd / mean) ** 2 / n
                (s0, v_s0), (s1, v_s1) = lm(*a[0]), lm(*a[1])
                (r0, v_r0), (r1, v_r1) = lm(*b[0]), lm(*b[1])
                seg_rel, rest_rel = math.exp(s1 - s0) - 1, math.exp(r1 - r0) - 1
            elif metric == "revenue":
                def lr(rev, n, sd):   # a sum of n order values: var(log sum) ~ (1 + CV^2) / n
                    mean = rev / n
                    return math.log(rev), (1 + (sd / mean) ** 2) / n
                (s0, v_s0), (s1, v_s1) = lr(*a[0]), lr(*a[1])
                (r0, v_r0), (r1, v_r1) = lr(*b[0]), lr(*b[1])
                seg_rel, rest_rel = math.exp(s1 - s0) - 1, math.exp(r1 - r0) - 1
            else:
                def lc(x):
                    x = x[0] + 0.5
                    return math.log(x), 1 / x
                (s0, v_s0), (s1, v_s1) = lc(a[0]), lc(a[1])
                (r0, v_r0), (r1, v_r1) = lc(b[0]), lc(b[1])
                seg_rel, rest_rel = math.exp(s1 - s0) - 1, math.exp(r1 - r0) - 1
        except (ValueError, ZeroDivisionError):
            return {"segment": segment, "metric": metric, "z": None, "seg_rel": None, "rest_rel": None,
                    "relative_to_rest": None, "did": None, "distinct": False}
        did = (s1 - s0) - (r1 - r0)
        se = math.sqrt(v_s0 + v_s1 + v_r0 + v_r1)
        z = did / se if se else None
        ratio = math.exp(did)   # the segment's change relative to the rest's, as a multiple
        return {"segment": segment, "metric": metric, "seg_rel": seg_rel, "rest_rel": rest_rel, "did": did, "z": z,
                "relative_to_rest": ratio - 1,
                "distinct": bool(z is not None and abs(z) >= 3.0 and abs(ratio - 1) >= 0.15)}

    # ---- the funnel -----------------------------------------------------------------------------
    def funnel(self, cur: Window, base: Window, segment: dict | None = None) -> list[dict]:
        out = []
        for step in FUNNEL + ["conversion_rate"]:
            c = self.compare(step, cur, base, segment)
            out.append({"step": step, "label": METRICS[step].label, "base": c["base"]["value"], "cur": c["cur"]["value"],
                        "rel": c["rel"], "base_n": c["base"]["den"], "cur_n": c["cur"]["den"]})
        return out

    # ---- where is it? ---------------------------------------------------------------------------
    def breakdown(self, metric: str, dim: str, cur: Window, base: Window, segment: dict | None = None,
                  min_share: float = 0.0) -> dict:
        """Split the change in `metric` across the values of `dim`.

        Totals: each segment's contribution is its own change. Rates: the change is split exactly into a
        mix effect per segment ((share now - share before) x (segment rate before - overall rate before)) and
        a rate effect per segment (share now x change in the segment's rate); they sum to the total change.
        """
        m = METRICS[metric]
        den = m.den or "count(*)"
        rows = self.con.execute(f"""
            SELECT {dim} AS v,
                   {m.num} FILTER (WHERE {base.sql()}) AS n0, {den} FILTER (WHERE {base.sql()}) AS d0,
                   {m.num} FILTER (WHERE {cur.sql()}) AS n1, {den} FILTER (WHERE {cur.sql()}) AS d1
            FROM {self.t}
            WHERE ({base.sql()} OR {cur.sql()}) AND {seg_sql(segment)}
            GROUP BY 1""").fetchall()
        rows = [(v, float(n0 or 0), float(d0 or 0), float(n1 or 0), float(d1 or 0)) for v, n0, d0, n1, d1 in rows]
        N0, D0 = sum(r[1] for r in rows), sum(r[2] for r in rows)
        N1, D1 = sum(r[3] for r in rows), sum(r[4] for r in rows)
        segs = []
        if m.is_rate:
            R0, R1 = (N0 / D0 if D0 else 0.0), (N1 / D1 if D1 else 0.0)
            total = R1 - R0
            for v, n0, d0, n1, d1 in rows:
                w0, w1 = (d0 / D0 if D0 else 0), (d1 / D1 if D1 else 0)
                r0 = n0 / d0 if d0 else R0
                r1 = n1 / d1 if d1 else r0
                mix, rate = (w1 - w0) * (r0 - R0), w1 * (r1 - r0)
                segs.append({"value": v, "share_base": w0, "share_cur": w1, "base": (n0 / d0 if d0 else None),
                             "cur": (n1 / d1 if d1 else None), "base_den": d0, "cur_den": d1,
                             "mix_effect": mix, "rate_effect": rate, "contribution": mix + rate})
            mix_total = sum(s["mix_effect"] for s in segs)
            out = {"metric": metric, "dimension": dim, "base": R0, "cur": R1, "delta": total, "mix_total": mix_total,
                   "rate_total": total - mix_total}
        else:
            total = N1 - N0
            for v, n0, _, n1, _ in rows:
                segs.append({"value": v, "base": n0, "cur": n1, "rel": (n1 - n0) / n0 if n0 else None,
                             "share_base": n0 / N0 if N0 else 0, "share_cur": n1 / N1 if N1 else 0,
                             "contribution": n1 - n0})
            out = {"metric": metric, "dimension": dim, "base": N0, "cur": N1, "delta": total}
        for s in segs:
            s["share_of_change"] = s["contribution"] / total if total else None
            s["placeholder"] = s["value"] in PLACEHOLDERS
        segs.sort(key=lambda s: -abs(s["contribution"]))
        out["segments"] = [s for s in segs if max(s["share_base"], s["share_cur"]) >= min_share]
        return out

    def rank_dimensions(self, metric: str, cur: Window, base: Window, segment: dict | None = None) -> list[dict]:
        """Which dimension concentrates the change? (explanatory power and surprise, after Adtributor)

        concentration: the share of the total change carried by the dimension's top segment.
        surprise: Jensen-Shannon divergence between the segments' shares of the volume and their shares of the
        change. A change spread in proportion to volume (every segment moved alike) scores near 0.
        """
        ranked = []
        for dim in DIMENSIONS:
            b = self.breakdown(metric, dim, cur, base, segment)
            segs = [s for s in b["segments"] if s["share_of_change"] is not None]
            if not segs or not b["delta"]:
                continue
            top = max(segs, key=lambda s: s["contribution"] * (1 if b["delta"] > 0 else -1))
            vol = [s["share_base"] for s in segs]
            chg = [max(s["contribution"] * (1 if b["delta"] > 0 else -1), 0.0) for s in segs]
            tot = sum(chg) or 1.0
            ranked.append({"dimension": dim, "top_value": top["value"], "top_share_of_change": top["share_of_change"],
                           "top_volume_share": top["share_base"], "surprise": _js(vol, [c / tot for c in chg]),
                           "mix_share": (b.get("mix_total", 0) / b["delta"]) if METRICS[metric].is_rate else None})
        ranked.sort(key=lambda r: -(r["surprise"] * min(max(r["top_share_of_change"], 0), 1.5)))
        return ranked

    def pairs(self, metric: str, dims: list[str], cur: Window, base: Window, top: int = 8) -> list[dict]:
        """Two-dimension segments (e.g. iOS x Safari) ranked by their share of the change."""
        out = []
        for i, a in enumerate(dims):
            for b in dims[i + 1:]:
                bd = self.breakdown(metric, f"{a} || ' | ' || {b}", cur, base)
                for s in bd["segments"][:3]:
                    va, _, vb = str(s["value"]).partition(" | ")
                    out.append({"segment": {a: va, b: vb}, "share_of_change": s["share_of_change"],
                                "contribution": s["contribution"], "base": s.get("base"), "cur": s.get("cur"),
                                "volume_share": s["share_base"]})
        out.sort(key=lambda r: -abs(r["contribution"]))
        return out[:top]

    def scan(self, metric: str, dim: str, cur: Window, weeks: int = 4) -> dict:
        """versus_rest for every value of a dimension at once, plus each value's usual divergence.

        For each value: the difference-in-differences of this week against last week (segment minus rest, as in
        versus_rest), and the same statistic for each of the `weeks` previous week pairs. A value counts as distinct
        when |z| >= 3, it is at least 15% apart from the rest, and its divergence is at least twice its median
        divergence in the previous weeks: segments that drift from the rest every week (landing pages, new
        visitors) are not news.
        """
        m = METRICS[metric]
        ks = [k for k in range(weeks + 1, -1, -1) if cur.shift(-7 * k).start >= self.first_date()]
        wins = [cur.shift(-7 * k) for k in ks]
        case = " ".join(f"WHEN {w.sql()} THEN {i}" for i, w in enumerate(wins))
        lo, hi = wins[0].start, wins[-1].end
        if m.is_rate and m.fmt == "pct":
            agg = f"{m.num}, {m.den}, NULL"
        elif metric in ("aov", "revenue"):
            agg = "sum(revenue), sum(transactions) FILTER (WHERE transactions > 0), stddev_samp(revenue / nullif(transactions, 0))"
        elif metric == "revenue_per_session":
            agg = "sum(revenue), count(*), stddev_samp(revenue)"
        else:
            agg = f"{m.num}, NULL, NULL"
        rows = self.con.execute(f"""SELECT CASE {case} END AS wk, {dim} AS v, {agg} FROM {self.t}
                                    WHERE date BETWEEN DATE '{lo}' AND DATE '{hi}' GROUP BY 1, 2""").fetchall()
        cells: dict = {}
        for wk, v, a, b, c in rows:
            if wk is None:
                continue
            cells.setdefault(v, {})[wk] = (float(a or 0), float(b or 0), float(c or 0))
        totals = {}
        for v, byw in cells.items():
            for wk, (a, b, c) in byw.items():
                t = totals.setdefault(wk, [0.0, 0.0, []])
                t[0] += a
                t[1] += b
                t[2].append((a, b, c))

        def stat(a, b, c):
            """(log value, variance) for one cell."""
            if m.is_rate and m.fmt == "pct":
                x, n = a + 0.5, b + 1.0
                return math.log(x / (n - x)), 1 / x + 1 / (n - x)
            if metric == "aov":
                mean = a / b
                return math.log(mean), (c / mean) ** 2 / b
            if metric == "revenue":
                mean = a / b
                return math.log(a), (1 + (c / mean) ** 2) / b
            if metric == "revenue_per_session":
                mean = a / b
                return math.log(mean), (c / mean) ** 2 / b
            x = a + 0.5
            return math.log(x), 1 / x

        def rest(wk, v):
            a, b, _ = totals[wk][0] - cells[v].get(wk, (0, 0, 0))[0], totals[wk][1] - cells[v].get(wk, (0, 0, 0))[1], 0
            # spread of order values for the rest: pooled from the other cells, weighted by orders
            others = [cc for vv, byw in cells.items() if vv != v for w2, cc in byw.items() if w2 == wk]
            sd = (sum(cc[2] * cc[1] for cc in others) / max(sum(cc[1] for cc in others), 1)) if others else 0
            return a, b, sd

        def did(v, w0, w1):
            try:
                s0, vs0 = stat(*cells[v][w0])
                s1, vs1 = stat(*cells[v][w1])
                r0, vr0 = stat(*rest(w0, v))
                r1, vr1 = stat(*rest(w1, v))
            except (KeyError, ValueError, ZeroDivisionError):
                return None
            d = (s1 - s0) - (r1 - r0)
            return d, d / math.sqrt(vs0 + vs1 + vr0 + vr1), s1 - s0, r1 - r0
        out = {}
        last = len(wins) - 1
        for v in cells:
            now = did(v, last - 1, last)
            if now is None:
                continue
            hist = [did(v, i - 1, i) for i in range(1, last)]
            hist = [abs(h[0]) for h in hist if h is not None]
            typical = sorted(hist)[len(hist) // 2] if hist else None
            d, z, ds, dr = now
            unusual = typical is None or len(hist) < 2 or abs(d) >= 2 * typical
            out[v] = {"segment": {dim: v}, "metric": metric, "did": d, "z": z, "relative_to_rest": math.exp(d) - 1,
                      "seg_rel": math.exp(ds) - 1, "rest_rel": math.exp(dr) - 1, "typical_did": typical,
                      "unusual": unusual,
                      "distinct": bool(abs(z) >= 3.0 and abs(math.exp(d) - 1) >= 0.15 and unusual)}
        return out

    def impact(self, metric: str, segment: dict, cur: Window, base: Window) -> float:
        """How much of the metric's week-over-week change comes from this segment behaving differently from the
        rest of the site, as a share of last week's value.

        Counterfactual: had the segment's numerator and denominator grown like the rest's, what would the metric be
        this week? impact = (actual - counterfactual) / last week's value. A segment that moved like everything
        else has an impact near zero, however large it is.
        """
        m = METRICS[metric]
        ins, out = seg_sql(segment), f"NOT ({seg_sql(segment)})"

        def parts(cond, w):
            r = self.con.execute(f"SELECT {m.num}, {m.den or '1'} FROM {self.t} WHERE {w.sql()} AND {cond}").fetchone()
            return float(r[0] or 0), float(r[1] or 0)
        (nsb, dsb), (nsc, dsc) = parts(ins, base), parts(ins, cur)
        (nrb, drb), (nrc, drc) = parts(out, base), parts(out, cur)
        if not m.den:
            g = nrc / nrb if nrb else 1.0
            total = nsb + nrb
            return (nsc - nsb * g) / total if total else 0.0
        gn, gd = (nrc / nrb if nrb else 1.0), (drc / drb if drb else 1.0)
        if not (dsc + drc) or not (dsb + drb) or not (drc + dsb * gd):
            return 0.0
        q_cur, q_base = (nsc + nrc) / (dsc + drc), (nsb + nrb) / (dsb + drb)
        q_cf = (nrc + nsb * gn) / (drc + dsb * gd)
        return (q_cur - q_cf) / q_base if q_base else 0.0

    def change_point_relative(self, metric: str, segment: dict, w: Window, earliest=None) -> dict | None:
        """The first day the segment started behaving differently from the rest: the best single split of the
        daily ratio (segment / rest), so seasonality that hits everyone alike cancels out."""
        seg_days = {d["date"]: d["value"] for d in self.daily(metric, w, segment)}
        rest = self.con.execute(f"""SELECT date, {METRICS[metric].num}, {METRICS[metric].den or '1'} FROM {self.t}
                                    WHERE {w.sql()} AND NOT ({seg_sql(segment)}) GROUP BY date ORDER BY date""").fetchall()
        rest_days = {d: (float(n or 0) / float(dd) if dd else None) for d, n, dd in rest}
        days = [(d, seg_days[d] / rest_days[d]) for d in sorted(seg_days)
                if seg_days.get(d) is not None and rest_days.get(d)]
        if len(days) < 5:
            return None
        vals = [v for _, v in days]
        best = None
        for k in range(2, len(vals) - 1):
            if earliest is not None and days[k][0] < earliest:
                continue          # a week-over-week question: the change starts in the question week
            a, b = vals[:k], vals[k:]
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            sse = sum((x - ma) ** 2 for x in a) + sum((x - mb) ** 2 for x in b)
            if best is None or sse < best[0]:
                best = (sse, k, ma, mb)
        if best is None:
            return None
        _, k, ma, mb = best
        return {"start": days[k][0], "before": ma, "after": mb, "rel": (mb - ma) / ma if ma else None}

    # ---- when did it start? -----------------------------------------------------------------------
    def daily(self, metric: str, w: Window, segment: dict | None = None) -> list[dict]:
        m = METRICS[metric]
        cols = f"{m.num} AS num" + (f", {m.den} AS den" if m.den else ", NULL AS den")
        rows = self.con.execute(f"""SELECT date, {cols} FROM {self.t} WHERE {w.sql()} AND {seg_sql(segment)}
                                    GROUP BY date ORDER BY date""").fetchall()
        return [{"date": d, "num": float(n or 0), "den": (float(dd) if dd is not None else None),
                 "value": (float(n or 0) / float(dd) if dd else None) if m.den else float(n or 0)} for d, n, dd in rows]

    def change_point(self, metric: str, w: Window, segment: dict | None = None) -> dict | None:
        """The single split of the daily series that best separates 'before' from 'after' (least squares)."""
        days = [d for d in self.daily(metric, w, segment) if d["value"] is not None]
        if len(days) < 4:
            return None
        vals = [d["value"] for d in days]
        best = None
        for k in range(2, len(vals) - 1):
            a, b = vals[:k], vals[k:]
            ma, mb = sum(a) / len(a), sum(b) / len(b)
            sse = sum((x - ma) ** 2 for x in a) + sum((x - mb) ** 2 for x in b)
            if best is None or sse < best[0]:
                best = (sse, k, ma, mb)
        _, k, ma, mb = best
        return {"start": days[k]["date"], "before": ma, "after": mb, "rel": (mb - ma) / ma if ma else None}


def _js(p: list[float], q: list[float]) -> float:
    def kl(a, b):
        return sum(x * math.log2(x / y) for x, y in zip(a, b) if x > 0 and y > 0)
    sp, sq = sum(p) or 1, sum(q) or 1
    p, q = [x / sp for x in p], [x / sq for x in q]
    m = [(x + y) / 2 for x, y in zip(p, q)]
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)
