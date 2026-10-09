# Data

- `sessions.parquet`, `daily_events.parquet`: built by `scripts/build_tables.py` from Google's public GA4
  demo-store export, `bigquery-public-data.ga4_obfuscated_sample_ecommerce` (Google Merchandise Store,
  1 Nov 2020 - 31 Jan 2021, obfuscated by Google; documentation page under CC BY 4.0). The raw events come from
  a flattened copy published at https://huggingface.co/datasets/gizdevans/ga4-bq-flattened-data (MIT), checked
  against its author's published count (4,295,584 events x 60 columns, 92 days). Hashes: `manifest.json`.
- `natural_weeks.json`: the survey of the natural data (`scripts/survey_weeks.py`).
- `calendar.json`: public US retail dates in the window.
- `benchmark.json`: the frozen benchmark (`scripts/build_benchmark.py`; rules in `BENCHMARK_GUIDE.md`).
