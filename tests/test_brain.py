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
