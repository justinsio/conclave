from datetime import datetime, timezone, timedelta
from loop import run_once, post_age_minutes
from brain import Draft

def _post(minutes_old, ac=0, pid="11111111-1111-1111-1111-111111111111"):
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_old)).isoformat()
    return {"id": pid, "title": "t", "body": "b", "token_budget": 150,
            "category": "coding", "answer_count": ac, "created_at": created}

class FakeBrain:
    def __init__(self, conf): self._c = conf
    async def answer(self, post, context, purpose="answer"):
        return Draft(body="ans", confidence=self._c, approach="a", intent_match="full", token_count=2)

class FakeClient:
    def __init__(self, posts, threads=None):
        self._posts = posts; self._threads = threads or []
        self.actions = []
        self.agent_id = "me"
    async def list_threads(self, cats): return self._threads
    async def list_unanswered_posts(self, category): return list(self._posts)
    async def corpus_similar(self, q, category, k=3): return []
    async def post_answer(self, post_id, body, confidence, token_count, intent_match):
        self.actions.append(("answer", post_id)); return {"id": "a"}
    async def open_thread(self, source_post_id):
        self.actions.append(("open_thread", source_post_id)); return {"thread_id": "t"}

async def test_skips_posts_under_draft_threshold(config):
    client = FakeClient(posts=[_post(2)])
    action = await run_once(client, FakeBrain(0.99), config)
    assert action == "idle" and client.actions == []

async def test_high_confidence_posts_solo_answer(config):
    client = FakeClient(posts=[_post(7)])
    action = await run_once(client, FakeBrain(0.90), config)
    assert ("answer", "11111111-1111-1111-1111-111111111111") in client.actions
    assert action == "answered"

async def test_mid_confidence_opens_thread(config):
    client = FakeClient(posts=[_post(7)])
    action = await run_once(client, FakeBrain(0.70), config)
    assert ("open_thread", "11111111-1111-1111-1111-111111111111") in client.actions
    assert action == "opened_thread"

async def test_low_confidence_does_nothing(config):
    client = FakeClient(posts=[_post(7)])
    action = await run_once(client, FakeBrain(0.10), config)
    assert client.actions == [] and action == "idle"

async def test_overdue_post_answered_regardless_of_low_confidence(config):
    client = FakeClient(posts=[_post(20)])
    action = await run_once(client, FakeBrain(0.10), config)
    assert ("answer", "11111111-1111-1111-1111-111111111111") in client.actions

async def test_existing_thread_takes_priority_over_posts(config, monkeypatch):
    import discussion
    played = {}
    async def fake_play(*a, **k): played["yes"] = True
    monkeypatch.setattr(discussion, "play", fake_play)
    client = FakeClient(posts=[_post(7)], threads=[{"thread_id": "t", "source_post_id": "p",
                        "source_post_category": "coding", "coordinator_id": "me"}])
    async def get_thread(tid): return {"source_post_id": "p"}
    client.get_thread = get_thread
    async def get_post(pid): return _post(7)
    client.get_post = get_post
    action = await run_once(client, FakeBrain(0.9), config)
    assert played.get("yes") and action == "played_thread"
