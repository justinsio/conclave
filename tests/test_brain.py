import json
import logging

from brain import Brain, estimate_tokens, parse_generation, Draft
from providers.base import FakeProvider


def test_estimate_tokens_is_positive():
    assert estimate_tokens("a b c d") >= 1


def test_parse_generation_reads_json():
    raw = '{"body":"use a set","confidence":0.9,"approach":"set dedup","intent_match":"full"}'
    d = parse_generation(raw)
    assert d.body == "use a set" and d.confidence == 0.9 and d.intent_match == "full"


def test_parse_generation_extracts_trailing_json():
    raw = 'thinking...\n{"body":"x","confidence":0.7,"approach":"y","intent_match":"partial"}'
    d = parse_generation(raw)
    assert d.confidence == 0.7


def test_parse_generation_invalid_returns_none():
    assert parse_generation("not json at all") is None


def test_parse_generation_clamps_and_defaults_bad_intent():
    raw = '{"body":"x","confidence":1.4,"approach":"y","intent_match":"bogus"}'
    d = parse_generation(raw)
    assert d.confidence == 1.0 and d.intent_match == "partial"


async def test_brain_answer_builds_prompt_and_returns_draft():
    provider = FakeProvider(['{"body":"answer body","confidence":0.88,"approach":"a","intent_match":"full"}'])
    brain = Brain(provider, specialty="coding")
    post = {"title": "Dedup a list", "body": "preserve order", "token_budget": 150}
    draft = await brain.answer(post, context=[])
    assert isinstance(draft, Draft)
    assert draft.token_count > 0
    system, user = provider.calls[0]
    assert "AGENT_CONTENT_START" in user
    assert "coding" in system.lower()


async def test_brain_answer_injects_rag_context_when_present():
    provider = FakeProvider(['{"body":"b","confidence":0.9,"approach":"a","intent_match":"full"}'])
    brain = Brain(provider, specialty="research")
    post = {"title": "t", "body": "b", "token_budget": 200}
    ctx = [{"question_text": "prior q", "answer_text": "prior a"}]
    await brain.answer(post, context=ctx)
    _, user = provider.calls[0]
    assert "prior a" in user


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
