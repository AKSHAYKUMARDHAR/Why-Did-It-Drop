"""One-time extract of Google's public GA4 demo-store dataset into local Parquet tables.

    python -m scripts.extract_ga4            # needs bq-key.json (a service-account key) in the repo root

Source: bigquery-public-data.ga4_obfuscated_sample_ecommerce, Google Merchandise Store web events,
1 November 2020 to 31 January 2021 (obfuscated; some fields hold placeholders such as "<Other>").
Writes three tables to data/raw/ and their row counts and SHA-256 hashes to data/extract_manifest.json:

- sessions.parquet: one row per session (user_pseudo_id + ga_session_id), with its first event's
  device, OS, browser, country, the user's acquisition medium and source, the landing page, the session
  number (1 = new user), and counts of each funnel event, revenue and transactions.
- purchase_items.parquet: one row per item in a purchase event (category, price, quantity, revenue).
- daily_events.parquet: event counts per day, event name and device/browser/country/medium, for the
  tracking-health checks.

BigQuery sandbox (no billing) is enough: the queries scan a few GB of the 1 TB monthly free allowance.
"""
import hashlib
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
SRC = "`bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`"
WINDOW = "_TABLE_SUFFIX BETWEEN '20201101' AND '20210131'"

EVENTS = f"""
  SELECT
    PARSE_DATE('%Y%m%d', event_date) AS date,
    event_timestamp,
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS session_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_number') AS session_number,
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS page_location,
    event_name,
    device.category AS device,
    device.operating_system AS os,
    device.web_info.browser AS browser,
    geo.country AS country,
    traffic_source.medium AS medium,
    traffic_source.source AS source,
    ecommerce.purchase_revenue_in_usd AS revenue,
    ecommerce.transaction_id AS transaction_id,
    items
  FROM {SRC}
  WHERE {WINDOW}
"""

QUERIES = {
    "sessions": f"""
WITH ev AS ({EVENTS})
SELECT
  MIN(date) AS date,
  MIN(event_timestamp) AS start_us,
  user_pseudo_id,
  session_id,
  ARRAY_AGG(device ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS device,
  ARRAY_AGG(os ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS os,
  ARRAY_AGG(browser ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS browser,
  ARRAY_AGG(country ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS country,
  ARRAY_AGG(medium ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS medium,
  ARRAY_AGG(source ORDER BY event_timestamp LIMIT 1)[OFFSET(0)] AS source,
  ARRAY_AGG(IF(event_name = 'page_view', page_location, NULL) IGNORE NULLS ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS landing_page,
  MIN(session_number) AS session_number,
  COUNTIF(event_name = 'page_view') AS page_view,
  COUNTIF(event_name = 'view_item') AS view_item,
  COUNTIF(event_name = 'add_to_cart') AS add_to_cart,
  COUNTIF(event_name = 'begin_checkout') AS begin_checkout,
  COUNTIF(event_name = 'add_shipping_info') AS add_shipping_info,
  COUNTIF(event_name = 'add_payment_info') AS add_payment_info,
  COUNTIF(event_name = 'purchase') AS purchase,
  COUNT(DISTINCT IF(event_name = 'purchase', transaction_id, NULL)) AS transactions,
  SUM(IF(event_name = 'purchase', revenue, 0)) AS revenue,
  COUNT(*) AS events
FROM ev
WHERE session_id IS NOT NULL
GROUP BY user_pseudo_id, session_id
""",
    "purchase_items": f"""
WITH ev AS ({EVENTS})
SELECT
  date, user_pseudo_id, session_id, transaction_id,
  item.item_id, item.item_name, item.item_category,
  item.price_in_usd AS price, item.quantity, item.item_revenue_in_usd AS item_revenue
FROM ev, UNNEST(items) AS item
WHERE event_name = 'purchase'
""",
    "daily_events": f"""
WITH ev AS ({EVENTS})
SELECT date, event_name, device, browser, country, medium, COUNT(*) AS events,
       COUNT(DISTINCT user_pseudo_id) AS users
FROM ev
GROUP BY date, event_name, device, browser, country, medium
""",
}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    key = ROOT / "bq-key.json"
    if not key.exists():
        print("bq-key.json not found in the repo root (a Google Cloud service-account key with BigQuery access).")
        return 1
    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(key))
    from google.cloud import bigquery

    project = json.loads(key.read_text(encoding="utf-8"))["project_id"]
    client = bigquery.Client(project=project)
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "bigquery-public-data.ga4_obfuscated_sample_ecommerce", "window": "2020-11-01 to 2021-01-31",
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tables": {}}
    for name, sql in QUERIES.items():
        t0 = time.time()
        job = client.query(sql)
        df = job.result().to_dataframe(create_bqstorage_client=False)
        path = RAW / f"{name}.parquet"
        df.to_parquet(path, index=False)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["tables"][name] = {"rows": len(df), "columns": list(df.columns), "sha256": digest,
                                    "bytes_scanned": job.total_bytes_processed}
        print(f"  {name}: {len(df):,} rows, {job.total_bytes_processed / 1e9:.2f} GB scanned, {time.time() - t0:.0f}s")
    with open(ROOT / "data" / "extract_manifest.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print("wrote data/extract_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
