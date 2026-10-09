"""Download the raw GA4 demo-store events (a flattened copy of Google's public BigQuery dataset).

    python -m scripts.fetch_ga4

Saves data/raw/full_flatten_data.csv (1.8 GB, gitignored) and checks its SHA-256 against data/manifest.json
once that exists. Source: https://huggingface.co/datasets/gizdevans/ga4-bq-flattened-data (MIT), made from
bigquery-public-data.ga4_obfuscated_sample_ecommerce by the author of github.com/dgizdevans/ga4-bq-flatten.
"""
import json
import pathlib
import sys

import httpx

from scripts.build_tables import RAW, ROOT, sha

URL = "https://huggingface.co/datasets/gizdevans/ga4-bq-flattened-data/resolve/main/full_flatten_data.csv"


def main() -> int:
    RAW.parent.mkdir(parents=True, exist_ok=True)
    if not RAW.exists():
        with httpx.stream("GET", URL, follow_redirects=True, timeout=600) as r, open(RAW, "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    manifest = ROOT / "data" / "manifest.json"
    if manifest.exists():
        want = json.loads(manifest.read_text(encoding="utf-8"))["raw"]["sha256"]
        if sha(RAW) != want:
            print("hash differs from data/manifest.json: the published copy has changed")
            return 1
    print(f"{RAW} ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
