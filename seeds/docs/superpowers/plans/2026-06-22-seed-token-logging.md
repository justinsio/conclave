# Seed Token Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture real LLM token counts at the seed's provider boundary and emit them as structured logs, so Test A's cost projection (A2) uses actual usage instead of a `len/4` heuristic.

**Architecture:** `LLMProvider.complete()` returns a `Completion(text, prompt_tokens, completion_tokens, model)` instead of a bare string. `Brain.answer()` propagates it, sets the answer's real `token_count`, and emits a structured `llm_usage` log line tagged by purpose. Both generation call sites (`loop.py` solo answer, `discussion.py` draft) pass their purpose. A standalone `scripts/aggregate_usage.py` sums the logs into per-purpose token means and a cost projection.

**Tech Stack:** Python 3.12, `httpx`, stdlib `logging`/`json`/`argparse`, `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). Tests mock httpx via `httpx.MockTransport`.

**Test command (run from repo root `F:/ObsidianAI/conclave-seeds`):**
```bash
<python3.12> -m pytest
```

---

## Existing contract (verified 2026-06-22)

- `providers/base.py` — `LLMProvider.complete(system, user) -> str`; `FakeProvider(responses: list[str])`.
- `providers/ollama.py` — `POST /api/chat` non-stream; returns `resp.json()["message"]["content"]`. Ollama also returns `prompt_eval_count` + `eval_count`.
- `providers/deepseek.py` — OpenAI-compatible; returns `["choices"][0]["message"]["content"]`. DeepSeek also returns `usage.{prompt_tokens,completion_tokens}`.
- `brain.py` — `Draft` is a mutable `@dataclass` with `token_count`; `answer(post, context)` calls `provider.complete()` and `parse_generation()` (which sets `token_count = estimate_tokens(body)`).
- `loop.py:34` — `draft = await brain.answer(target, context)` (solo); posts at `loop.py:40` `client.post_answer(..., draft.token_count, ...)`.
- `discussion.py:25` — `draft = await brain.answer(post, context=[])`; submits at `discussion.py:29` `client.submit_draft(..., token_count=draft.token_count)`.
- `observability.py` — `setup_logging(seed_name)` attaches a stdout `StreamHandler` to root; tests assert via `capsys`.

## File map

- Modify: `providers/base.py`, `providers/ollama.py`, `providers/deepseek.py`, `brain.py`, `observability.py`, `loop.py`, `discussion.py`
- Create: `scripts/__init__.py`, `scripts/aggregate_usage.py`, `tests/test_aggregate_usage.py`
- Update tests: `tests/test_providers_base.py`, `tests/test_providers_ollama.py`, `tests/test_providers_deepseek.py`, `tests/test_brain.py`, `tests/test_observability.py`

Three tasks, each leaving the suite green:
1. `Completion` type + providers return it (brain consumes `.text`).
2. Usage logging + real `token_count` + purpose tags.
3. Aggregator script.

---

## Task 1: `Completion` value type + providers return it

**Files:** Modify `providers/base.py`, `providers/ollama.py`, `providers/deepseek.py`, `brain.py`; update `tests/test_providers_base.py`, `tests/test_providers_ollama.py`, `tests/test_providers_deepseek.py`.

- [ ] **Step 1: Rewrite the provider tests for the new return type**

`tests/test_providers_base.py`:
```python
import pytest
from providers.base import LLMProvider, FakeProvider, Completion


async def test_fake_provider_returns_queued_completion():
    p = FakeProvider(["hello"], prompt_tokens=12, completion_tokens=3, model="m")
    c = await p.complete("sys", "user")
    assert isinstance(c, Completion)
    assert c.text == "hello"
    assert c.prompt_tokens == 12 and c.completion_tokens == 3 and c.model == "m"
    assert p.calls == [("sys", "user")]


def test_llmprovider_is_abstract():
    with pytest.raises(TypeError):
        LLMProvider()
```

`tests/test_providers_ollama.py`:
```python
import httpx
from providers.ollama import OllamaProvider


async def test_ollama_returns_completion_with_token_counts():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "local answer"},
                                         "prompt_eval_count": 50, "eval_count": 12})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OllamaProvider(base_url="http://o", model="llama3.1:8b", http=http)
    c = await p.complete("s", "u")
    assert c.text == "local answer"
    assert c.prompt_tokens == 50 and c.completion_tokens == 12 and c.model == "llama3.1:8b"


async def test_ollama_missing_counts_default_zero():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "x"}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = OllamaProvider(base_url="http://o", model="m", http=http)
    c = await p.complete("s", "u")
    assert c.prompt_tokens == 0 and c.completion_tokens == 0
```

`tests/test_providers_deepseek.py`:
```python
import httpx
from providers.deepseek import DeepSeekProvider


async def test_deepseek_returns_completion_with_usage():
    captured = {}
    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "the answer"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 40}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = DeepSeekProvider(api_key="dk", base_url="https://api.deepseek.com",
                         model="deepseek-chat", http=http)
    c = await p.complete("system text", "user text")
    assert c.text == "the answer"
    assert c.prompt_tokens == 200 and c.completion_tokens == 40 and c.model == "deepseek-chat"
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer dk"


async def test_deepseek_missing_usage_defaults_zero():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    p = DeepSeekProvider(api_key="dk", base_url="https://api.deepseek.com",
                         model="deepseek-chat", http=http)
    c = await p.complete("s", "u")
    assert c.prompt_tokens == 0 and c.completion_tokens == 0
```

- [ ] **Step 2: Run the provider tests to verify they fail**

Run: `<python3.12> -m pytest tests/test_providers_base.py tests/test_providers_ollama.py tests/test_providers_deepseek.py -q`
Expected: FAIL — `ImportError: cannot import name 'Completion'`.

- [ ] **Step 3: Add `Completion` + update `FakeProvider` in `providers/base.py`**

Replace the whole file with:
```python
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str) -> "Completion":
        """Return the model's completion (text + token usage) for a system+user prompt."""


class FakeProvider(LLMProvider):
    """Test double — returns queued responses in order, records calls."""
    def __init__(self, responses: list[str], *, prompt_tokens: int = 0,
                 completion_tokens: int = 0, model: str = "fake"):
        self._responses = list(responses)
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._model = model
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> Completion:
        self.calls.append((system, user))
        text = self._responses.pop(0) if self._responses else "{}"
        return Completion(text=text, prompt_tokens=self._prompt_tokens,
                          completion_tokens=self._completion_tokens, model=self._model)
```

- [ ] **Step 4: Update `providers/ollama.py`**

Replace the `complete` method (keep the constructor) — full file:
```python
from __future__ import annotations
import httpx
from providers.base import Completion, LLMProvider


class OllamaProvider(LLMProvider):
    """Local Ollama completion seam — swap in via LLM_PROVIDER=ollama."""
    def __init__(self, base_url: str, model: str, http: httpx.AsyncClient | None = None):
        self._model = model
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=120.0)

    async def complete(self, system: str, user: str) -> Completion:
        resp = await self._http.post(
            f"{self._base}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return Completion(
            text=data["message"]["content"],
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            model=self._model,
        )
```

- [ ] **Step 5: Update `providers/deepseek.py`**

Full file:
```python
from __future__ import annotations
import httpx
from providers.base import Completion, LLMProvider


class DeepSeekProvider(LLMProvider):
    """OpenAI-compatible chat completion against DeepSeek."""
    def __init__(self, api_key: str, base_url: str, model: str, http: httpx.AsyncClient | None = None):
        self._key = api_key
        self._model = model
        self._base = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=60.0)

    async def complete(self, system: str, user: str) -> Completion:
        resp = await self._http.post(
            f"{self._base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "stream": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        return Completion(
            text=data["choices"][0]["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            model=self._model,
        )
```

- [ ] **Step 6: Update `brain.py` to consume `.text`**

In `brain.py`, change the `answer` method body only (signature unchanged this task):
```python
    async def answer(self, post: dict, context: list[dict]) -> Draft | None:
        system = _SYSTEM.format(specialty=self._specialty)
        completion = await self._provider.complete(system, self._user_prompt(post, context))
        return parse_generation(completion.text)
```

- [ ] **Step 7: Run the full suite to verify green**

Run: `<python3.12> -m pytest -q`
Expected: all pass (provider tests updated; `test_brain.py` still passes because `brain` uses `completion.text` and `token_count` still comes from `estimate_tokens`).

- [ ] **Step 8: Commit**
```bash
git add providers/base.py providers/ollama.py providers/deepseek.py brain.py tests/test_providers_base.py tests/test_providers_ollama.py tests/test_providers_deepseek.py
git commit -m "feat: providers return Completion with token usage"
```

---

## Task 2: Usage logging + real token_count + purpose tags

**Files:** Modify `observability.py`, `brain.py`, `loop.py`, `discussion.py`; update `tests/test_observability.py`, `tests/test_brain.py`.

- [ ] **Step 1: Write the failing observability test**

Add to `tests/test_observability.py`:
```python
import json
from observability import setup_logging, log_llm_usage


def test_log_llm_usage_emits_json_line(capsys):
    setup_logging("coding")
    log_llm_usage("answer", "qwen2.5:3b", 820, 140)
    out = capsys.readouterr().out
    line = [l for l in out.splitlines() if "llm_usage" in l][-1]
    payload = json.loads(line[line.index("{"):])
    assert payload["event"] == "llm_usage"
    assert payload["purpose"] == "answer"
    assert payload["model"] == "qwen2.5:3b"
    assert payload["prompt_tokens"] == 820 and payload["completion_tokens"] == 140
    assert payload["total_tokens"] == 960
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<python3.12> -m pytest tests/test_observability.py -q`
Expected: FAIL — `ImportError: cannot import name 'log_llm_usage'`.

- [ ] **Step 3: Add `log_llm_usage` to `observability.py`**

Add the `json` import at the top (alongside the existing `import logging`, `import sys`) and append this function:
```python
import json  # add near the existing imports


def log_llm_usage(purpose: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Emit one structured JSON line per generation for offline cost aggregation."""
    logging.getLogger("seed.usage").info(json.dumps({
        "event": "llm_usage",
        "purpose": purpose,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }))
```

- [ ] **Step 4: Run the observability test to verify it passes**

Run: `<python3.12> -m pytest tests/test_observability.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing brain tests**

Add to `tests/test_brain.py`:
```python
import json
import logging
from observability import log_llm_usage  # noqa: F401 (ensures logger name exists)


async def test_brain_uses_real_completion_tokens():
    provider = FakeProvider(
        ['{"body":"answer body","confidence":0.88,"approach":"a","intent_match":"full"}'],
        prompt_tokens=820, completion_tokens=140, model="qwen2.5:3b")
    brain = Brain(provider, specialty="coding")
    draft = await brain.answer({"title": "t", "body": "b", "token_budget": 150},
                               context=[], purpose="answer")
    assert draft.token_count == 140


async def test_brain_falls_back_to_estimate_when_no_completion_tokens():
    provider = FakeProvider(
        ['{"body":"some longer body text here","confidence":0.8,"approach":"a","intent_match":"full"}'],
        prompt_tokens=0, completion_tokens=0)
    brain = Brain(provider, specialty="coding")
    draft = await brain.answer({"title": "t", "body": "b"}, context=[], purpose="answer")
    assert draft.token_count > 0  # estimate fallback, never zero (server requires > 0)


async def test_brain_logs_usage_with_purpose(caplog):
    caplog.set_level(logging.INFO, logger="seed.usage")
    provider = FakeProvider(
        ['{"body":"b","confidence":0.9,"approach":"a","intent_match":"full"}'],
        prompt_tokens=820, completion_tokens=140, model="qwen2.5:3b")
    brain = Brain(provider, specialty="coding")
    await brain.answer({"title": "t", "body": "b"}, context=[], purpose="discussion_draft")
    rec = [r for r in caplog.records if r.name == "seed.usage"][-1]
    payload = json.loads(rec.message)
    assert payload["purpose"] == "discussion_draft"
    assert payload["model"] == "qwen2.5:3b"
    assert payload["total_tokens"] == 960
```

- [ ] **Step 6: Run brain tests to verify they fail**

Run: `<python3.12> -m pytest tests/test_brain.py -q`
Expected: FAIL — `answer()` got an unexpected keyword `purpose` (and `token_count` mismatch).

- [ ] **Step 7: Update `brain.py` — purpose, real token_count, usage log**

Add `import observability` at the top of `brain.py` (after the existing imports), and replace the `answer` method:
```python
    async def answer(self, post: dict, context: list[dict], purpose: str = "answer") -> Draft | None:
        system = _SYSTEM.format(specialty=self._specialty)
        completion = await self._provider.complete(system, self._user_prompt(post, context))
        observability.log_llm_usage(
            purpose, completion.model, completion.prompt_tokens, completion.completion_tokens)
        draft = parse_generation(completion.text)
        if draft is None:
            return None
        if completion.completion_tokens > 0:
            draft.token_count = completion.completion_tokens
        return draft
```

- [ ] **Step 8: Tag the two call sites**

In `loop.py`, line 34 — change:
```python
    draft = await brain.answer(target, context, purpose="answer")
```
In `discussion.py`, line 25 — change:
```python
    draft = await brain.answer(post, context=[], purpose="discussion_draft")
```

- [ ] **Step 9: Run the full suite to verify green**

Run: `<python3.12> -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**
```bash
git add observability.py brain.py loop.py discussion.py tests/test_observability.py tests/test_brain.py
git commit -m "feat: log real LLM token usage per generation; real answer token_count"
```

---

## Task 3: Usage aggregator

**Files:** Create `scripts/__init__.py`, `scripts/aggregate_usage.py`, `tests/test_aggregate_usage.py`.

- [ ] **Step 1: Write the failing aggregator test**

`tests/test_aggregate_usage.py`:
```python
from scripts.aggregate_usage import aggregate, parse_lines


def test_aggregate_means_and_cost_by_purpose():
    records = [
        {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 800, "completion_tokens": 100},
        {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 1000, "completion_tokens": 200},
        {"event": "llm_usage", "purpose": "discussion_draft", "prompt_tokens": 2000, "completion_tokens": 500},
    ]
    out = aggregate(records, input_rate=0.000001, output_rate=0.000002)
    ans = out["by_purpose"]["answer"]
    assert ans["count"] == 2
    assert ans["mean_prompt_tokens"] == 900
    assert ans["mean_completion_tokens"] == 150
    assert ans["mean_total_tokens"] == 1050
    assert abs(ans["cost_usd"] - (1800 * 0.000001 + 300 * 0.000002)) < 1e-12
    dd = out["by_purpose"]["discussion_draft"]
    assert dd["count"] == 1
    expected_total = (1800 * 0.000001 + 300 * 0.000002) + (2000 * 0.000001 + 500 * 0.000002)
    assert abs(out["total_cost_usd"] - expected_total) < 1e-12


def test_aggregate_ignores_non_usage_events():
    records = [{"event": "other"},
               {"event": "llm_usage", "purpose": "answer", "prompt_tokens": 10, "completion_tokens": 5}]
    out = aggregate(records, 0.0, 0.0)
    assert set(out["by_purpose"]) == {"answer"}


def test_parse_lines_tolerates_log_prefix_and_junk():
    lines = [
        '2026-06-22 [coding] INFO seed.usage: {"event":"llm_usage","purpose":"answer","prompt_tokens":5,"completion_tokens":3}',
        'not json at all',
        '',
    ]
    recs = parse_lines(lines)
    assert len(recs) == 1
    assert recs[0]["completion_tokens"] == 3
```

- [ ] **Step 2: Run it to verify it fails**

Run: `<python3.12> -m pytest tests/test_aggregate_usage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts'`.

- [ ] **Step 3: Create the package marker**

`scripts/__init__.py`:
```python
```
(empty file)

- [ ] **Step 4: Write `scripts/aggregate_usage.py`**
```python
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
```

- [ ] **Step 5: Run the full suite to verify green**

Run: `<python3.12> -m pytest -q`
Expected: all pass (original 38 + the new/updated tests).

- [ ] **Step 6: Commit**
```bash
git add scripts/__init__.py scripts/aggregate_usage.py tests/test_aggregate_usage.py
git commit -m "feat: usage aggregator for A2 cost numbers"
```

---

## Out of this plan

- **Wiring the aggregator into the harness's cost report** — for now it's a standalone operator step (`docker logs <seed> | python scripts/aggregate_usage.py`). The harness already captures real per-answer `token_count` via the API thanks to the bonus.
- **Confirming live DeepSeek input/output rates** before a real cost run (defaults in the CLI are placeholders).
- **Pushing to Gitea** — confirm with Justin per the push rule.

## Self-review notes

- **Spec coverage:** Completion type (T1) ✓; ollama/deepseek/fake usage (T1) ✓; brain propagate + token_count bonus + fallback (T2) ✓; purpose tags at both call sites (T2) ✓; structured log (T2) ✓; aggregator with separate input/output rates (T3) ✓; all tests listed in the spec are present.
- **Type consistency:** `Completion(text, prompt_tokens, completion_tokens, model)` used identically across base/ollama/deepseek/brain/tests; `log_llm_usage(purpose, model, prompt_tokens, completion_tokens)` signature matches its call in `brain.answer`; `aggregate(records, input_rate, output_rate)` and `parse_lines(lines)` match the test imports.
- **Server-constraint safety:** the `completion_tokens > 0` guard preserves the heuristic fallback so a posted answer never carries `token_count=0` (server enforces `token_count > 0`).
