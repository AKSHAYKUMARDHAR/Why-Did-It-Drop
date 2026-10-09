import datetime as dt

import duckdb
import pytest

from eval.score import score, segment_match
from wdid import incidents
from wdid.engine import Engine, week_of
from wdid.evidence import Facts, fits_type
from wdid.investigate import trace_numbers

D = dt.date


@pytest.fixture()
def con():
    """Two weeks of synthetic sessions: 2 OS x 2 countries, 200 sessions a day each, 3% converting."""
    c = duckdb.connect()
    c.execute("""
        CREATE TABLE base_sessions AS
        SELECT DATE '2020-11-30' + (d % 14) * INTERVAL 1 DAY AS date, 0 AS start_us,
               'u' || i AS user_pseudo_id, i AS session_id,
               CASE WHEN i % 2 = 0 THEN 'desktop' ELSE 'mobile' END AS device,
               CASE WHEN i % 2 = 0 THEN 'Web' ELSE 'iOS' END AS os, 'Chrome' AS browser,
               CASE WHEN i % 3 = 0 THEN 'India' ELSE 'United States' END AS country,
               'organic' AS medium, 'google' AS source, 'Home' AS landing, '' AS landing_url, 'new' AS user_type,
               1 AS page_view, CASE WHEN (i // 2) % 4 = 0 THEN 1 ELSE 0 END AS view_item,
               CASE WHEN i % 8 = 0 THEN 1 ELSE 0 END AS add_to_cart,
               CASE WHEN i % 16 = 0 THEN 1 ELSE 0 END AS begin_checkout, 0 AS add_shipping_info,
               CASE WHEN i % 16 = 0 THEN 1 ELSE 0 END AS add_payment_info,
               CASE WHEN i % 32 = 0 THEN 1 ELSE 0 END AS purchase,
               CASE WHEN i % 32 = 0 THEN 1 ELSE 0 END AS transactions,
               CASE WHEN i % 32 = 0 THEN 60.0 ELSE 0 END AS revenue, 3 AS events
        FROM range(0, 11200) t(i), (SELECT i // 800 AS d) x
        WHERE true""")
    c.execute("UPDATE base_sessions SET date = DATE '2020-11-30' + (session_id // 800) * INTERVAL 1 DAY")
    incidents.plant(c, None)
    return c


def test_plant_product_break_zeroes_later_steps(con):
    inc = {"type": "product_break", "segment": {"os": "iOS"}, "step": "begin_checkout", "start": "2020-12-07",
           "end": "2020-12-13", "fraction": 1.0, "seed": 1}
    incidents.plant(con, inc)
    left = con.execute("""SELECT sum(begin_checkout), sum(purchase), sum(revenue) FROM sessions
                          WHERE os = 'iOS' AND date >= DATE '2020-12-07'""").fetchone()
    assert left == (0, 0, 0)
    other = con.execute("SELECT sum(purchase) FROM sessions WHERE os = 'Web' AND date >= DATE '2020-12-07'").fetchone()[0]
    assert other > 0


def test_versus_rest_flags_a_planted_segment_and_not_the_others(con):
    incidents.plant(con, {"type": "product_break", "segment": {"os": "iOS"}, "step": "view_item", "start": "2020-12-07",
                          "end": "2020-12-13", "fraction": 0.9, "seed": 2})
    e = Engine(con)
    cur, base = week_of(D(2020, 12, 7)), week_of(D(2020, 11, 30))
    ios = e.versus_rest("view_item_rate", {"os": "iOS"}, cur, base)
    india = e.versus_rest("view_item_rate", {"country": "India"}, cur, base)
    assert ios["distinct"] and ios["z"] < -3
    assert not india["distinct"]


def test_traffic_loss_removes_sessions(con):
    before = con.execute("SELECT count(*) FROM sessions WHERE country = 'India'").fetchone()[0]
    incidents.plant(con, {"type": "traffic_loss", "segment": {"country": "India"}, "start": "2020-11-30",
                          "end": "2020-12-13", "fraction": 0.5, "seed": 3})
    after = con.execute("SELECT count(*) FROM sessions WHERE country = 'India'").fetchone()[0]
    assert 0.4 * before < after < 0.6 * before


def test_fits_type_needs_an_absolute_worsening():
    prof = [{"metric": "sessions", "distinct": True, "z_value": -8.0, "seg_rel": -0.05}]
    assert fits_type(prof) is None                 # only grew less than the rest: a mirror, not a loss
    prof[0]["seg_rel"] = -0.4
    assert fits_type(prof) == "traffic_loss"
    assert fits_type([{"metric": "checkout_completion_rate", "distinct": True, "z_value": -5, "seg_rel": -0.7}]) == "product_break"
    assert fits_type([{"metric": "conversion_rate", "distinct": True, "z_value": 5, "seg_rel": 0.7}]) is None


def test_numbers_must_come_from_facts():
    F = Facts()
    a = F.add("Conversion rate, this week", 0.019, "pct")
    pack = {"facts": F, "candidates": [{"segment": {"os": "iOS 14"}}], "health": []}
    text, removed, unknown = trace_numbers("Conversion is {" + a + "} on iOS 14, down 20% since Dec 11 {F99}.", pack)
    assert "1.90%" in text and "iOS 14" in text and "20%" not in text
    assert removed == 2 and unknown == 1


def test_scoring_outcomes():
    case = {"id": "X", "kind": "planted", "expected": {"verdict": "incident", "label": "clear", "type": "product_break",
                                                       "segment": {"os": "iOS"}, "start": "2020-12-09", "check_start": True}}
    right = score(case, {"verdict": "incident", "type": "product_break", "segment": {"os": "iOS"}, "start_date": "2020-12-10"})
    wrong = score(case, {"verdict": "incident", "type": "traffic_loss", "segment": {"os": "iOS"}, "start_date": "2020-12-09"})
    missed = score(case, {"verdict": "no_change"})
    assert (right["outcome"], wrong["outcome"], missed["outcome"]) == ("right", "wrong", "missed")
    quiet = {"id": "Q", "kind": "quiet", "expected": {"verdict": "no_change", "label": "quiet", "accept": ["no_change", "broad"]}}
    assert score(quiet, {"verdict": "incident"})["outcome"] == "false_alarm"
    assert score(quiet, {"verdict": "broad"})["outcome"] == "right"
    small = {"id": "S", "kind": "planted", "expected": {**case["expected"], "label": "too_small", "accept": ["no_change", "cant_tell"]}}
    assert score(small, {"verdict": "no_change"})["outcome"] == "right"


def test_segment_match_uses_overlap():
    assert segment_match({"os": "iOS"}, {"os": "iOS"}, {"jaccard": 1.0, "inside": 1.0, "coverage": 1.0})
    assert segment_match({"os": "iOS", "browser": "Safari"}, {"os": "iOS"}, {"jaccard": 0.89, "inside": 1.0, "coverage": 0.89})
    assert not segment_match({"device": "desktop"}, {"os": "Web"}, {"jaccard": 0.46, "inside": 0.63, "coverage": 0.63})
    assert segment_match({}, {}, None) and not segment_match({"os": "iOS"}, {}, None)
