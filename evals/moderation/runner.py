"""Run the labeled eval set against the real Haiku gate, N passes per item.

Only items that clear structural_precheck reach the model (mirrors the pipeline
short-circuit - structural rejects cost no API call). Each item is run N times
because the observed Haiku failure mode is *stochastic* compliance: a single pass
measures a coin flip, not a rate.

Output: results.jsonl - one row per (item, pass) with the raw gate verdict, so the
scorer can sweep confidence-floor thresholds offline without re-calling the API.

Usage (spends Anthropic API money - real Haiku calls):
    python -m evals.moderation.runner --passes 5
    python -m evals.moderation.runner --passes 5 --dataset evals/moderation/dataset.jsonl \
        --out evals/moderation/results.jsonl

Run from the repo root with the conclave venv active and ANTHROPIC_API_KEY set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Awaitable, Callable

from app.services.moderation import ModerationVerdict, moderate_content, structural_precheck

_HERE = Path(__file__).parent
_DEFAULT_DATASET = _HERE / "dataset.jsonl"
_DEFAULT_CORPUS = _HERE / "harmful_corpus.jsonl"   # git-ignored; user-populated
_DEFAULT_OUT = _HERE / "results.jsonl"

VerdictFn = Callable[[str], Awaitable[ModerationVerdict]]


def load_dataset(path: Path) -> list[dict]:
    """Load one JSONL file. Committed dataset.jsonl holds ONLY non-harmful slices.

    If a sibling harmful_corpus.jsonl exists (git-ignored - see README), its items
    are merged in so the clearly_harmful / egregious bars can be scored. Without it,
    those slices are simply absent and the scorer reports only the benign bars.
    """
    items = _read_jsonl(path)
    if path == _DEFAULT_DATASET and _DEFAULT_CORPUS.exists():
        corpus = _read_jsonl(_DEFAULT_CORPUS)
        seen = {it["id"] for it in items}
        for it in corpus:
            if it["id"] in seen:
                raise ValueError(f"harmful_corpus.jsonl reuses id {it['id']!r} from dataset.jsonl")
        items.extend(corpus)
    return items


def _read_jsonl(path: Path) -> list[dict]:
    items: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} - invalid JSON: {exc}") from exc
    return items


def _combined_text(item: dict) -> str:
    """Post path is the superset (title + body); answers submit body only."""
    title = item.get("title") or ""
    body = item.get("body") or ""
    return f"{title}\n{body}" if title else body


async def run(
    items: list[dict],
    passes: int,
    verdict_fn: VerdictFn = moderate_content,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Return one result row per (item, pass). verdict_fn is injectable for testing."""
    results: list[dict] = []
    for item in items:
        title, body = item.get("title") or "", item.get("body") or ""
        reject = structural_precheck(title, body)
        for p in range(passes):
            if reject is not None:
                results.append(
                    {"id": item["id"], "pass": p, "structural": reject,
                     "decision": None, "confidence": None, "category": None}
                )
                continue
            verdict = await verdict_fn(_combined_text(item))
            results.append(
                {"id": item["id"], "pass": p, "structural": None,
                 "decision": verdict.decision, "confidence": verdict.confidence,
                 "category": verdict.category}
            )
        if progress:
            progress(item["id"])
    return results


def write_results(results: list[dict], out: Path) -> None:
    with out.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row) + "\n")


async def _stub_verdict(text: str) -> ModerationVerdict:
    """Deterministic over-permissive gate for smoke-testing (no API calls, no spend).

    Returns PASS@0.92 for everything, so a --stub run exercises the whole
    runner->scorer path and the scorer will (correctly) flag harmful leaks.
    """
    return ModerationVerdict("PASS", 0.92, "safe", "stub (no API call)", "stub-model")


async def _main() -> None:
    ap = argparse.ArgumentParser(description="Run the moderation eval set against real Haiku.")
    ap.add_argument("--passes", type=int, default=5, help="passes per item (default 5)")
    ap.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    ap.add_argument("--stub", action="store_true",
                    help="use a fake gate (no API calls) to smoke-test the harness")
    args = ap.parse_args()

    # Record the model's RAW verdict (apply_floor=False) so the scorer stays the single
    # place the confidence floor is applied and can still sweep thresholds offline.
    async def _raw_gate(text: str) -> ModerationVerdict:
        return await moderate_content(text, apply_floor=False)

    verdict_fn = _stub_verdict if args.stub else _raw_gate
    items = load_dataset(args.dataset)
    n_model = sum(1 for it in items if structural_precheck(it.get("title") or "", it.get("body") or "") is None)
    print(f"Loaded {len(items)} items; {n_model} reach the model "
          f"-> ~{n_model * args.passes} Haiku calls at {args.passes} passes.")

    done = [0]
    def _progress(item_id: str) -> None:
        done[0] += 1
        if done[0] % 10 == 0:
            print(f"  ...{done[0]}/{len(items)} items")

    results = await run(items, args.passes, verdict_fn=verdict_fn, progress=_progress)
    write_results(results, args.out)
    print(f"Wrote {len(results)} result rows -> {args.out}")
    print("Next: python -m evals.moderation.scorer")


if __name__ == "__main__":
    asyncio.run(_main())
