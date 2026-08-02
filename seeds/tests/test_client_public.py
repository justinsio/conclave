import httpx
from client import ConclaveClient
from tests.conftest import mock_client


async def test_connect_handshake_and_sets_agent_id(config):
    seen = []
    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path == "/v1/rules":
            return httpx.Response(200, json={"version": "1.0", "published_at": "x", "rules": [], "changelog": []})
        if req.url.path == "/v1/agents/connect":
            assert b'"rules_version_acknowledged":"1.0"' in req.content.replace(b" ", b"")
            return httpx.Response(200, json={"status": "connected", "agent_id": "a", "plan": "reader",
                "rank_score": 0, "rules_version": "1.0", "trial_ends_at": None, "message": "ok"})
        if req.url.path == "/v1/agents/me":
            return httpx.Response(200, json={"id": "agent-self-id"})
        return httpx.Response(404)
    c = ConclaveClient(config, http=mock_client(handler))
    await c.connect()
    assert ("GET", "/v1/rules") in seen and ("POST", "/v1/agents/connect") in seen
    assert c.agent_id == "agent-self-id"


async def test_list_unanswered_posts_filters_answered(config):
    def handler(req):
        assert "sort=unanswered" in str(req.url)
        return httpx.Response(200, json={"data": [
            {"id": "11111111-1111-1111-1111-111111111111", "category": "coding", "intent": "solution",
             "title": "t", "body": "b", "token_budget": 150, "tags": None, "allow_clarification": True,
             "status": "open", "visibility": "public", "answer_count": 0, "created_at": "2026-06-15T00:00:00Z"},
            {"id": "22222222-2222-2222-2222-222222222222", "category": "coding", "intent": "solution",
             "title": "t2", "body": "b2", "token_budget": 150, "tags": None, "allow_clarification": True,
             "status": "open", "visibility": "public", "answer_count": 2, "created_at": "2026-06-15T00:00:00Z"}],
            "pagination": {"next_cursor": None, "has_more": False, "count": 2}})
    c = ConclaveClient(config, http=mock_client(handler))
    posts = await c.list_unanswered_posts("coding")
    assert [p["answer_count"] for p in posts] == [0]


async def test_post_answer_sends_answercreate(config):
    captured = {}
    def handler(req):
        captured["json"] = req.content
        return httpx.Response(201, json={"id": "33333333-3333-3333-3333-333333333333",
            "post_id": "11111111-1111-1111-1111-111111111111", "body": "x", "confidence": 0.9,
            "token_count": 5, "intent_match": "full", "upvote_count": 0, "human_accepted": False,
            "references": [], "created_at": "2026-06-15T00:00:00Z"})
    c = ConclaveClient(config, http=mock_client(handler))
    r = await c.post_answer("11111111-1111-1111-1111-111111111111", "x", 0.9, 5, "full")
    assert b'"intent_match":"full"' in captured["json"].replace(b" ", b"")
    assert r["id"]


async def test_corpus_similar_returns_empty_gracefully(config):
    def handler(req):
        return httpx.Response(200, json={"data": [], "count": 0, "reason": "embeddings_unavailable"})
    c = ConclaveClient(config, http=mock_client(handler))
    assert await c.corpus_similar("q", "coding") == []
