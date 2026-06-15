from discussion import play
from brain import Draft


class FakeBrain:
    async def answer(self, post, context):
        return Draft(body="my draft", confidence=0.8, approach="a", intent_match="solution", token_count=2)


class FakeClient:
    def __init__(self, thread_detail):
        self.calls = []
        self._detail = thread_detail

    async def register(self, tid):
        self.calls.append(("register", tid))
        return {}

    async def submit_draft(self, tid, **kw):
        self.calls.append(("draft", kw))
        return {"draft_id": "d"}

    async def get_thread(self, tid):
        return self._detail

    async def endorse(self, tid, cid, note=None):
        self.calls.append(("endorse", cid))
        return {}

    async def conclude(self, tid, cid, ctype, note=None):
        self.calls.append(("conclude", cid, ctype))
        return {}


async def test_play_registers_and_drafts():
    detail = {"thread_id": "t", "status": "deliberating", "coordinator_id": "me",
              "contributions": [
                  {"id": "c_me", "agent_id": "me", "confidence": 0.8, "retracted": False},
                  {"id": "c_peer", "agent_id": "peer", "confidence": 0.9, "retracted": False}]}
    client = FakeClient(detail)
    summary = {"thread_id": "t", "source_post_id": "p", "coordinator_id": "me"}
    post = {"title": "t", "body": "b", "token_budget": 150}
    await play(client, FakeBrain(), summary, post, my_agent_id="me")
    kinds = [c[0] for c in client.calls]
    assert "register" in kinds and "draft" in kinds


async def test_coordinator_endorses_peer_then_concludes_on_leader():
    detail = {"thread_id": "t", "status": "deliberating", "coordinator_id": "me",
              "contributions": [
                  {"id": "c_me", "agent_id": "me", "confidence": 0.7, "retracted": False},
                  {"id": "c_peer", "agent_id": "peer", "confidence": 0.95, "retracted": False}]}
    client = FakeClient(detail)
    summary = {"thread_id": "t", "source_post_id": "p", "coordinator_id": "me"}
    await play(client, FakeBrain(), summary, {"title": "t", "body": "b", "token_budget": 150}, my_agent_id="me")
    assert ("endorse", "c_peer") in client.calls
    assert any(c[0] == "conclude" and c[1] == "c_peer" for c in client.calls)


async def test_participant_endorses_but_does_not_conclude():
    detail = {"thread_id": "t", "status": "deliberating", "coordinator_id": "someone_else",
              "contributions": [
                  {"id": "c_me", "agent_id": "me", "confidence": 0.7, "retracted": False},
                  {"id": "c_peer", "agent_id": "peer", "confidence": 0.95, "retracted": False}]}
    client = FakeClient(detail)
    summary = {"thread_id": "t", "source_post_id": "p", "coordinator_id": "someone_else"}
    await play(client, FakeBrain(), summary, {"title": "t", "body": "b", "token_budget": 150}, my_agent_id="me")
    assert ("endorse", "c_peer") in client.calls
    assert not any(c[0] == "conclude" for c in client.calls)


async def test_no_peer_means_no_endorse_or_conclude():
    detail = {"thread_id": "t", "status": "deliberating", "coordinator_id": "me",
              "contributions": [{"id": "c_me", "agent_id": "me", "confidence": 0.8, "retracted": False}]}
    client = FakeClient(detail)
    summary = {"thread_id": "t", "source_post_id": "p", "coordinator_id": "me"}
    await play(client, FakeBrain(), summary, {"title": "t", "body": "b", "token_budget": 150}, my_agent_id="me")
    assert not any(c[0] in ("endorse", "conclude") for c in client.calls)


async def test_still_blind_phase_returns_after_draft():
    detail = {"thread_id": "t", "status": "blind_phase", "coordinator_id": "me", "contributions": []}
    client = FakeClient(detail)
    summary = {"thread_id": "t", "source_post_id": "p", "coordinator_id": "me"}
    await play(client, FakeBrain(), summary, {"title": "t", "body": "b", "token_budget": 150}, my_agent_id="me")
    assert not any(c[0] in ("endorse", "conclude") for c in client.calls)
