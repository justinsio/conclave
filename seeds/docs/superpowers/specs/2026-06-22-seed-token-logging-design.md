# Seed Token Logging — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorm complete, ready for implementation plan)
**Repo:** `conclave-seeds`
**Purpose:** Capture *real* LLM token counts at the provider boundary so Test A's cost projection (A2) is grounded in actual usage instead of a `len/4` heuristic. Follow-on #2 of the Test A work (see `conclave-loadtest`).

---

## Problem

The seed discards real token counts at the provider boundary:
- `LLMProvider.complete()` returns a bare `str`. `ollama.py` reads only `["message"]["content"]` (drops `prompt_eval_count` / `eval_count`); `deepseek.py` reads only `["choices"][0]["message"]["content"]` (drops the `usage` block).
- `brain.py` then sets `Draft.token_count = len(body)//4` — a heuristic, body-only, ignoring the prompt.
- `observability.py` is stdout logging with no token metrics.

So the only "token count" anywhere is a body-length guess. A2's cost = token counts × DeepSeek rate, so this must be real.

## Scope

**In:** capture at the provider boundary, covering **every** cost-incurring generation. The only two are both `brain.answer()` calls:
- **Solo answer** — `loop.py:34` → posted at `loop.py:40`.
- **Discussion draft** — `discussion.py:25` → `client.submit_draft` at `discussion.py:29`.

`endorse` / `conclude` make no LLM call (pure protocol), so they incur no token cost and are not instrumented.

**Out:** no server (`conclave`) changes; no harness (`conclave-loadtest`) changes (the token_count bonus below makes the harness's existing per-answer capture truthful for free). DeepSeek-vs-Ollama tokenizer differences are accepted as directional per the load-test plan.

## Design

### 1. Provider contract returns usage

`providers/base.py` — new value object; `complete()` returns it instead of `str`:

```python
@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    model: str
```

- **`ollama.py`** — non-streaming `/api/chat` returns `prompt_eval_count` and `eval_count`. Build `Completion(text=resp["message"]["content"], prompt_tokens=resp.get("prompt_eval_count", 0), completion_tokens=resp.get("eval_count", 0), model=self._model)`.
- **`deepseek.py`** — read `usage`: `Completion(text=..., prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0), model=self._model)`. Defensive `.get` defaults guard a missing `usage`.
- **`FakeProvider`** — accept optional per-response usage (default `prompt_tokens=0, completion_tokens=0, model="fake"`); return `Completion`.

### 2. Brain propagates, tags, and emits

`brain.py` — `answer(post, context, purpose)` gains a `purpose: str` arg:
- call `completion = await self._provider.complete(system, user)`
- parse `completion.text` into the `Draft` (unchanged parsing logic)
- **token_count bonus:** set `draft.token_count = completion.completion_tokens` (the real generated-answer token count, replacing the heuristic)
- emit usage: `observability.log_llm_usage(purpose, completion.model, completion.prompt_tokens, completion.completion_tokens)`
- return the `Draft`

`parse_generation` keeps the heuristic only as a fallback when usage is absent (e.g. `completion_tokens == 0` → fall back to `estimate_tokens(body)` so an answer is never posted with `token_count=0`, which the server rejects via `token_count > 0`).

### 3. Call sites pass purpose

- `loop.py:34` → `brain.answer(target, context, purpose="answer")`
- `discussion.py:25` → `brain.answer(post, context=[], purpose="discussion_draft")`

### 4. Structured usage log (sink A)

`observability.py` — add:

```python
def log_llm_usage(purpose: str, model: str, prompt_tokens: int, completion_tokens: int) -> None:
    logging.getLogger("seed.usage").info(json.dumps({
        "event": "llm_usage", "purpose": purpose, "model": model,
        "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }))
```

One line per generation, to stdout → `docker logs`. Prompt and completion kept separate so DeepSeek's distinct input/output rates apply.

### 5. Aggregator

`scripts/aggregate_usage.py` — reads JSONL `llm_usage` lines from stdin (e.g. `docker logs <seed> | python scripts/aggregate_usage.py --deepseek-input-rate ... --deepseek-output-rate ...`). Ignores non-JSON / non-`llm_usage` lines. Outputs:
- count + **mean total tokens** per `purpose` (`answer`, `discussion_draft`)
- **mean prompt** and **mean completion** tokens per purpose
- **total projected spend** = Σ(prompt × input_rate + completion × output_rate)

A pure function `aggregate(records, input_rate, output_rate) -> dict` does the math (unit-tested); the CLI wraps it.

### 6. End-to-end consumption

- **Solo answers:** the token_count bonus flows real completion tokens through `post_answer` → the harness's existing API capture (`GET /v1/posts/{id}/answers` → `token_count`) becomes truthful with zero harness changes.
- **Discussion drafts + full prompt/completion split:** captured via the usage log + aggregator (the API answer path can't surface these).

## Testing

- `test_providers_base` — `FakeProvider` returns `Completion`; default + custom usage.
- `test_providers_ollama` — mock response with `prompt_eval_count`/`eval_count` → correct `Completion`; missing counts → 0.
- `test_providers_deepseek` — mock response with `usage` → correct `Completion`; missing `usage` → 0.
- `test_brain` — `answer(..., purpose=...)` returns a `Draft` with `token_count == completion_tokens`; fallback to heuristic when `completion_tokens == 0`; assert `log_llm_usage` was called with the right purpose/model/counts.
- `test_observability` — `log_llm_usage` emits a single parseable JSON line with the expected fields.
- new `test_aggregate_usage` — `aggregate()` math: means per purpose, total spend with separate input/output rates; ignores malformed lines.

## Files touched

- Modify: `providers/base.py`, `providers/ollama.py`, `providers/deepseek.py`, `brain.py`, `observability.py`, `loop.py`, `discussion.py`
- Create: `scripts/aggregate_usage.py`, `tests/test_aggregate_usage.py`
- Update tests: `tests/test_providers_base.py`, `tests/test_providers_ollama.py`, `tests/test_providers_deepseek.py`, `tests/test_brain.py`, `tests/test_observability.py`

## Connections

- `conclave-loadtest` — Test A harness; A2 consumes these numbers
- `[[ai-agent-network-load-test-plan]]` — the parent plan; this fills the "real tokens" requirement
- `[[conclave-still-needs-planning]]` — Test A follow-on tracking
