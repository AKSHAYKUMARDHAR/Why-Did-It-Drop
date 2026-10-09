"""Plant an incident in a copy of the real sessions, so the right answer is known by construction.

Each incident changes only the sessions it targets (a segment, from a start date), chosen by a seeded hash of
the session id, so the rest of the data stays real, including its natural noise and seasonality.

Types and what each changes:
- product_break   a funnel step stops working for part of a segment: sessions that reached the step lose it
                  and everything after it (a payment SDK bug on one OS, a broken cart on one browser).
- traffic_loss    a share of a channel's sessions disappears (a campaign ended, a ranking was lost).
- mix_shift       low-intent sessions are added to a segment: copies of its real bounced sessions, with new
                  ids (a bot burst, a cheap campaign). Every existing session is unchanged.
- tracking_dup    purchase events fire twice: revenue counted twice, orders (distinct transactions) unchanged.
- tracking_gap    checkout-start events stop firing on a segment while purchases continue (a tag bug).
- basket_change   revenue per order changes by a factor in a segment (a price cut, a discount, a currency bug).
- landing_break   (held-out only) sessions landing on one site section stop viewing products.
- units_bug       (held-out only) a segment's revenue is recorded x100 (a currency-units bug).
"""
import duckdb

STEPS = ["view_item", "add_to_cart", "begin_checkout", "add_shipping_info", "add_payment_info", "purchase"]
TYPES = ["product_break", "traffic_loss", "mix_shift", "tracking_dup", "tracking_gap", "basket_change",
         "landing_break", "units_bug"]
# What a correct diagnosis calls each planted type.
DIAGNOSIS = {"product_break": "product_break", "landing_break": "product_break", "traffic_loss": "traffic_loss",
             "mix_shift": "mix_shift", "tracking_dup": "tracking_issue", "tracking_gap": "tracking_issue",
             "units_bug": "tracking_issue", "basket_change": "basket_change"}


def _seg(segment: dict) -> str:
    return " AND ".join(f"{k} = '{str(v)}'" for k, v in segment.items())


def _pick(frac: float, seed: int) -> str:
    """A deterministic pseudo-random choice of `frac` of sessions."""
    return f"(hash(user_pseudo_id || '-' || session_id::VARCHAR || '-{seed}') % 1000000) < {int(frac * 1_000_000)}"


def plant(con: duckdb.DuckDBPyConnection, inc: dict | None, src: str = "base_sessions", dst: str = "sessions") -> None:
    """Create table `dst` as `src` with incident `inc` applied (None: an unchanged copy)."""
    con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT * FROM {src}")
    if not inc:
        return
    t, seg, seed = inc["type"], inc.get("segment") or {}, inc.get("seed", 1)
    where = f"date BETWEEN DATE '{inc['start']}' AND DATE '{inc['end']}'" + (f" AND {_seg(seg)}" if seg else "")
    f = inc.get("fraction", 1.0)
    if t in ("product_break", "landing_break"):
        step = inc["step"]
        later = STEPS[STEPS.index(step):]
        zero = ", ".join(f"{s} = 0" for s in later) + (", transactions = 0, revenue = 0" if "purchase" in later else "")
        con.execute(f"UPDATE {dst} SET {zero} WHERE {where} AND {step} > 0 AND {_pick(f, seed)}")
    elif t == "traffic_loss":
        con.execute(f"DELETE FROM {dst} WHERE {where} AND {_pick(f, seed)}")
    elif t == "mix_shift":
        # copies of the segment's real bounced sessions (no product view), `fraction` x the segment's daily volume
        con.execute(f"""
            INSERT INTO {dst}
            SELECT s.* REPLACE (s.user_pseudo_id || '-p{seed}-' || k::VARCHAR AS user_pseudo_id,
                                'new' AS user_type)
            FROM {src} s, range(0, {int(inc.get('copies', 1))}) r(k)
            WHERE {where.replace('date', 's.date', 1)} AND s.view_item = 0 AND s.purchase = 0
              AND {_pick(f, seed).replace('user_pseudo_id', 's.user_pseudo_id').replace('session_id', 's.session_id')}""")
    elif t == "tracking_dup":
        con.execute(f"UPDATE {dst} SET purchase = purchase * 2, revenue = revenue * 2 WHERE {where} AND purchase > 0")
    elif t == "tracking_gap":
        con.execute(f"UPDATE {dst} SET begin_checkout = 0 WHERE {where} AND begin_checkout > 0 AND {_pick(f, seed)}")
    elif t == "basket_change":
        con.execute(f"UPDATE {dst} SET revenue = revenue * {float(inc['factor'])} WHERE {where} AND purchase > 0")
    elif t == "units_bug":
        con.execute(f"UPDATE {dst} SET revenue = revenue * 100 WHERE {where} AND purchase > 0")
    else:
        raise ValueError(f"unknown incident type {t}")
