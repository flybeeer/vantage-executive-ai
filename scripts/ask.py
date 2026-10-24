"""Ask one question as one user and print what came back, with citations.

    python scripts/ask.py --user ceo "บอร์ดตัดสินใจอะไรเรื่องการขยายสาขา"
    python scripts/ask.py --user academic_director "รายได้สาขาบางนาเดือน ส.ค. เท่าไร"

Every call writes an audit row; `--audit` prints the latest rows afterwards.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vantage.observability import audit  # noqa: E402
from vantage.service import ask  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("question")
    parser.add_argument("--user", default="ceo", help="principal id from policy/policy.yaml")
    parser.add_argument("--audit", action="store_true", help="print the latest audit rows too")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    outcome = ask(args.user, args.question)
    print(f"\n[{outcome.decision}] route={outcome.route}  audit #{outcome.audit_id}")
    print(f"reason: {outcome.reason}")
    if outcome.message:
        print(f"\n{outcome.message}")
    for i, chunk in enumerate(outcome.chunks, start=1):
        snippet = " ".join(chunk.content.split())[:160]
        print(f"\n[{i}] {chunk.citation}   (score {chunk.score:.4f})\n    {snippet}…")

    if args.audit:
        print("\n--- audit log (newest first) ---")
        for row in audit.recent(limit=5):
            print(
                f"#{row['id']} {row['ts']:%Y-%m-%d %H:%M:%S} {row['principal']:<18} "
                f"ws={row['workspace']:<3} {row['decision']:<8} {row['question']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
