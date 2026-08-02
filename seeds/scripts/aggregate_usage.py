from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict
from typing import Iterable


def parse_lines(lines: Iterable[str]) -> list[dict]:
    """Extract JSON objects from log lines, tolerating a logging prefix and junk."""
    records = []
    for line in lines:
        i = line.find("{")
        if i < 0:
            continue
        try:
            records.append(json.loads(line[i:]))
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def aggregate(records: list[dict], input_rate: float, output_rate: float) -> dict:
    """Sum llm_usage records into per-purpose means and a cost projection.

    input_rate / output_rate are USD per prompt / completion token.
    """
    sums: dict[str, dict] = defaultdict(lambda: {"count": 0, "prompt": 0, "completion": 0})
    for r in records:
        if r.get("event") != "llm_usage":
            continue
        s = sums[r.get("purpose", "unknown")]
        s["count"] += 1
        s["prompt"] += int(r.get("prompt_tokens", 0))
        s["completion"] += int(r.get("completion_tokens", 0))

    out: dict = {"by_purpose": {}, "total_cost_usd": 0.0}
    for purpose, s in sums.items():
        n = s["count"]
        cost = s["prompt"] * input_rate + s["completion"] * output_rate
        out["by_purpose"][purpose] = {
            "count": n,
            "mean_prompt_tokens": s["prompt"] / n if n else 0.0,
            "mean_completion_tokens": s["completion"] / n if n else 0.0,
            "mean_total_tokens": (s["prompt"] + s["completion"]) / n if n else 0.0,
            "cost_usd": cost,
        }
        out["total_cost_usd"] += cost
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="aggregate_usage",
        description="Aggregate seed llm_usage log lines from stdin into A2 cost numbers.")
    ap.add_argument("--input-rate", type=float, default=0.00000027,
                    help="USD per prompt (input) token")
    ap.add_argument("--output-rate", type=float, default=0.0000011,
                    help="USD per completion (output) token")
    args = ap.parse_args()
    report = aggregate(parse_lines(sys.stdin), args.input_rate, args.output_rate)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
