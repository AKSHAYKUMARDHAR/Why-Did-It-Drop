"""Build the frozen analysis tables from the raw GA4 events.

    python -m scripts.fetch_ga4          # download the raw events (1.8 GB CSV) into data/raw/
    python -m scripts.build_tables       # verify it, then write data/sessions.parquet and data/daily_events.parquet

The raw file is a flattened copy of Google's public GA4 demo-store export
(bigquery-public-data.ga4_obfuscated_sample_ecommerce, 1 Nov 2020 - 31 Jan 2021), published on Hugging Face
(gizdevans/ga4-bq-flattened-data, MIT) by the author of github.com/dgizdevans/ga4-bq-flatten, who reports
4,295,584 rows x 60 columns, 1:1 with the source events. This script checks those figures before building
anything. The alternative route straight from BigQuery is scripts/extract_ga4.py.

Tables written (committed, with hashes in data/manifest.json):
- sessions.parquet: one row per session (user_pseudo_id + ga_session_id). Dimensions come from the session's
  first event; the channel is the session's own source/medium where the event recorded one, otherwise the
  user's first-touch source/medium; the landing page is grouped by site section.
- daily_events.parquet: event counts per day x event name x device x browser x country x medium, for the
  tracking-health checks.
"""
import hashlib
import json
import pathlib
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "full_flatten_data.csv"
EXPECTED_ROWS, EXPECTED_COLS, EXPECTED_DAYS = 4_295_584, 60, 92

LANDING_GROUP = r"""
CASE
  WHEN lp IS NULL THEN '(none)'
  WHEN regexp_matches(lp, '/(basket|cart)\.html') THEN 'Cart'
  WHEN regexp_matches(lp, '(checkout|/payment|/review)') THEN 'Checkout'
  WHEN regexp_matches(lp, '(signin|myaccount|registersuccess|/login)') THEN 'Account'
  WHEN regexp_matches(lp, '/store\.html|^https?://[^/]+/?(\?.*)?$') THEN 'Home'
  WHEN regexp_matches(lp, '/Google\+Redesign/([^/?]+)') THEN replace(regexp_extract(lp, '/Google\+Redesign/([^/?]+)', 1), '+', ' ')
  WHEN regexp_matches(lp, '/search') THEN 'Search'
  ELSE 'Other'
END
"""


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not RAW.exists():
        print(f"{RAW} not found: run python -m scripts.fetch_ga4 first")
        return 1
    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE ev AS SELECT * FROM read_csv('{RAW.as_posix()}', header = true, all_varchar = true,
                                                  sample_size = -1, parallel = true)""")
    # The published CSV joins several part files, each with its own header line: drop the repeats.
    headers = con.execute("SELECT count(*) FROM ev WHERE event_date = 'event_date'").fetchone()[0]
    con.execute("DELETE FROM ev WHERE event_date = 'event_date'")
    print(f"dropped {headers} repeated header lines")
    rows = con.execute("SELECT count(*) FROM ev").fetchone()[0]
    cols = len(con.execute("SELECT * FROM ev LIMIT 0").description)
    days = con.execute("SELECT count(DISTINCT event_date), min(event_date), max(event_date) FROM ev").fetchone()
    print(f"raw: {rows:,} rows x {cols} columns, {days[0]} days ({days[1]} - {days[2]})")
    if (rows, cols, days[0]) != (EXPECTED_ROWS, EXPECTED_COLS, EXPECTED_DAYS):
        print(f"does not match the published {EXPECTED_ROWS:,} x {EXPECTED_COLS}, {EXPECTED_DAYS} days: stopping")
        return 1

    con.execute(f"""
    CREATE TABLE e AS SELECT
      strptime(event_date, '%Y%m%d')::DATE AS date,
      CAST(event_timestamp AS BIGINT) AS ts,
      event_name, user_pseudo_id,
      TRY_CAST(ga_session_id AS BIGINT) AS session_id,
      TRY_CAST(ga_session_number AS BIGINT) AS session_number,
      device_category AS device, device_os AS os, browser, country,
      NULLIF(ep_medium, '') AS s_medium, NULLIF(ep_source, '') AS s_source,
      traffic_medium AS u_medium, traffic_source AS u_source,
      page_location,
      TRY_CAST(ecom_revenue_usd AS DOUBLE) AS revenue,
      NULLIF(ecom_transaction_id, '') AS transaction_id
    FROM ev""")
    con.execute("DROP TABLE ev")
    sessions = con.sql(f"""
    WITH s AS (
      SELECT user_pseudo_id, session_id,
        min(date) AS date, min(ts) AS start_us,
        arg_min(device, ts) AS device, arg_min(os, ts) AS os, arg_min(browser, ts) AS browser,
        arg_min(country, ts) AS country,
        coalesce(arg_min(s_medium, ts) FILTER (WHERE s_medium IS NOT NULL), arg_min(u_medium, ts)) AS medium,
        coalesce(arg_min(s_source, ts) FILTER (WHERE s_source IS NOT NULL), arg_min(u_source, ts)) AS source,
        arg_min(page_location, ts) FILTER (WHERE event_name = 'page_view') AS lp,
        min(session_number) AS session_number,
        count(*) FILTER (WHERE event_name = 'page_view') AS page_view,
        count(*) FILTER (WHERE event_name = 'view_item') AS view_item,
        count(*) FILTER (WHERE event_name = 'add_to_cart') AS add_to_cart,
        count(*) FILTER (WHERE event_name = 'begin_checkout') AS begin_checkout,
        count(*) FILTER (WHERE event_name = 'add_shipping_info') AS add_shipping_info,
        count(*) FILTER (WHERE event_name = 'add_payment_info') AS add_payment_info,
        count(*) FILTER (WHERE event_name = 'purchase') AS purchase,
        count(DISTINCT transaction_id) FILTER (WHERE event_name = 'purchase') AS transactions,
        coalesce(sum(revenue) FILTER (WHERE event_name = 'purchase'), 0) AS revenue,
        count(*) AS events
      FROM e WHERE session_id IS NOT NULL
      GROUP BY user_pseudo_id, session_id)
    SELECT date, start_us, user_pseudo_id, session_id, device, os, browser, country, medium, source,
           {LANDING_GROUP} AS landing, lp AS landing_url,
           CASE WHEN session_number = 1 THEN 'new' ELSE 'returning' END AS user_type,
           page_view, view_item, add_to_cart, begin_checkout, add_shipping_info, add_payment_info,
           purchase, transactions, revenue, events
    FROM s ORDER BY start_us""")
    sessions.write_parquet(str(ROOT / "data" / "sessions.parquet"), compression="zstd")
    no_session = con.execute("SELECT count(*) FROM e WHERE session_id IS NULL").fetchone()[0]
    con.execute(f"""COPY (SELECT date, event_name, device, browser, country,
                               coalesce(s_medium, u_medium) AS medium, count(*) AS events,
                               count(DISTINCT user_pseudo_id) AS users
                        FROM e GROUP BY ALL ORDER BY ALL)
                   TO '{(ROOT / "data" / "daily_events.parquet").as_posix()}' (FORMAT parquet, COMPRESSION zstd)""")
    out = {}
    for name in ("sessions", "daily_events"):
        p = ROOT / "data" / f"{name}.parquet"
        n = con.execute(f"SELECT count(*) FROM '{p.as_posix()}'").fetchone()[0]
        out[name] = {"rows": n, "bytes": p.stat().st_size, "sha256": sha(p)}
        print(f"  {name}: {n:,} rows, {p.stat().st_size / 1e6:.1f} MB")
    manifest = {
        "source": "bigquery-public-data.ga4_obfuscated_sample_ecommerce (Google Merchandise Store, obfuscated)",
        "window": "2020-11-01 to 2021-01-31",
        "raw": {"file": "data/raw/full_flatten_data.csv",
                "from": "https://huggingface.co/datasets/gizdevans/ga4-bq-flattened-data (MIT)",
                "rows": rows, "columns": cols, "days": days[0], "repeated_header_lines_dropped": headers, "sha256": sha(RAW),
                "events_without_session_id": no_session},
        "tables": out,
    }
    with open(ROOT / "data" / "manifest.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"events without a session id (left out of sessions): {no_session:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
