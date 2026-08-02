import httpx
from client import ConclaveClient
from tests.conftest import mock_client

TID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


async def test_list_threads_passes_category(config):
    def handler(req):
        assert req.url.path == "/internal/threads"
        return httpx.Response(200, json={"data": [{"thread_id": TID, "source_post_id": None,
            "source_post_category": "coding", "source_post_intent": "solution", "source_post_title": "t",
            "coordinator_id": CID, "status": "open", "contribution_count": 0,
            "deadline": "2026-06-15T00:12:00Z", "time_to_deadline_seconds": 600,
            "promoted_to_coordinator": False}], "count": 1})
    c = ConclaveClient(config, http=mock_client(handler))
    threads = await c.list_threads(["coding", "general"])
    assert threads[0]["thread_id"] == TID


async def test_open_thread_posts_source_post_id(config):
    def handler(req):
        assert b"source_post_id" in req.content
        return httpx.Response(201, json={"thread_id": TID, "source_post_id": None,
            "coordinator_id": CID, "status": "blind_phase", "blind_phase_ends_at": "2026-06-15T00:01:30Z",
            "deadline": "2026-06-15T00:12:00Z", "elevated_risk": False, "framing_alert": False,
            "created_at": "2026-06-15T00:00:00Z"})
    c = ConclaveClient(config, http=mock_client(handler))
    t = await c.open_thread("11111111-1111-1111-1111-111111111111")
    assert t["thread_id"] == TID


async def test_submit_draft_and_endorse_and_conclude(config):
    calls = []
    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(201, json={"ok": True})
    c = ConclaveClient(config, http=mock_client(handler))
    await c.register(TID)
    await c.submit_draft(TID, body="b", confidence=0.8, approach="a", intent_match="solution", token_count=3)
    await c.endorse(TID, CID, note="agree")
    await c.conclude(TID, CID, "consensus", note="winner")
    assert calls == [f"/internal/threads/{TID}/register",
                     f"/internal/threads/{TID}/draft",
                     f"/internal/threads/{TID}/endorse",
                     f"/internal/threads/{TID}/conclude"]
