"""Ingest the corpus declared in `data/manifest.yaml`.

Prints a per-file table so the provenance the demo claims — hash, version,
workspace, data date — is visible at ingest time rather than asserted in a
README. Run: `python scripts/ingest.py` (or `make ingest`).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vantage.config import get_settings  # noqa: E402
from vantage.ingestion.pipeline import ingest  # noqa: E402
from vantage.ingestion.provenance import sha256_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data" / "manifest.yaml",
        help="manifest to ingest (default: data/manifest.yaml)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / "data" / "raw",
        help="directory the manifest's file names are relative to",
    )
    args = parser.parse_args()

    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(message)s")
    result = ingest(args.manifest, args.data_root)

    header = f"{'file':<32} {'ver':<4} {'ws':<6} {'data date':<12} {'sha256':<10} {'stored'}"
    print()
    print(header)
    print("-" * len(header))
    for item in result["detail"]:
        digest = sha256_file(args.data_root / item["file"])[:8]
        stored = (
            f"{item['rows']} rows → {item['table']}"
            if item["kind"] == "tabular"
            else f"{item['chunks']} chunks / {item['pages']} pages"
        )
        print(
            f"{item['file']:<32} {item['version']:<4} "
            f"{','.join(item['workspaces']):<6} {item['data_date']:<12} {digest:<10} {stored}"
        )
    print()
    print(f"{result['files']} files, {result['chunks']} chunks indexed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
