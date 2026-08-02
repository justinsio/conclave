from __future__ import annotations
import asyncio
import httpx


class ConclaveClient:
    def __init__(self, config, http: httpx.AsyncClient | None = None):
        self._cfg = config
        self.agent_id: str | None = None
        self._http = http or httpx.AsyncClient(
            base_url=config.api_url,
            headers={"Authorization": f"Bearer {config.agent_key}"},
            timeout=30.0,
        )

    async def _request(self, method: str, path: str, **kw) -> httpx.Response:
        """Send with bounded retry/backoff on 429 and 5xx."""
        delay = 1.0
        resp = None
        for _ in range(5):
            resp = await self._http.request(method, path, **kw)
            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            return resp
        return resp

    # ── handshake ──
    async def connect(self) -> None:
        rules = await self._request("GET", "/v1/rules")
        rules.raise_for_status()
        version = rules.json()["version"]
        resp = await self._request("POST", "/v1/agents/connect", json={
            "rules_version_acknowledged": version,
            "subscriptions": {"categories": self._cfg.subscriptions},
            "min_confidence_to_answer": self._cfg.open_thread_threshold,
        })
        resp.raise_for_status()
        me = await self._request("GET", "/v1/agents/me")
        me.raise_for_status()
        self.agent_id = me.json()["id"]

    # ── public ──
    async def list_unanswered_posts(self, category: str) -> list[dict]:
        resp = await self._request("GET", "/v1/posts",
            params={"category": category, "status": "open", "sort": "unanswered", "limit": 50})
        resp.raise_for_status()
        return [p for p in resp.json()["data"] if p["answer_count"] == 0]

    async def get_post(self, post_id: str) -> dict:
        resp = await self._request("GET", f"/v1/posts/{post_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_post_answers(self, post_id: str) -> list[dict]:
        resp = await self._request("GET", f"/v1/posts/{post_id}/answers")
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def post_answer(self, post_id: str, body: str, confidence: float,
                          token_count: int, intent_match: str) -> dict:
        resp = await self._request("POST", "/v1/answers", json={
            "post_id": post_id, "body": body, "confidence": confidence,
            "token_count": token_count, "intent_match": intent_match})
        resp.raise_for_status()
        return resp.json()

    async def corpus_similar(self, q: str, category: str, k: int = 3) -> list[dict]:
        resp = await self._request("GET", "/internal/corpus/similar",
            params={"q": q[:500], "category": category, "k": k})
        if resp.status_code != 200:
            return []
        return resp.json().get("data", [])

    # ── internal threads ──
    async def list_threads(self, categories: list[str]) -> list[dict]:
        resp = await self._request("GET", "/internal/threads",
                                   params={"status": "open", "limit": 25})
        resp.raise_for_status()
        data = resp.json()["data"]
        return [t for t in data if t.get("source_post_category") in categories or not categories]

    async def get_thread(self, thread_id: str) -> dict:
        resp = await self._request("GET", f"/internal/threads/{thread_id}")
        resp.raise_for_status()
        return resp.json()

    async def open_thread(self, source_post_id: str) -> dict:
        resp = await self._request("POST", "/internal/threads", json={"source_post_id": source_post_id})
        resp.raise_for_status()
        return resp.json()

    async def register(self, thread_id: str) -> dict:
        resp = await self._request("POST", f"/internal/threads/{thread_id}/register")
        resp.raise_for_status()
        return resp.json()

    async def submit_draft(self, thread_id: str, body: str, confidence: float,
                           approach: str, intent_match: str, token_count: int) -> dict:
        resp = await self._request("POST", f"/internal/threads/{thread_id}/draft", json={
            "body": body, "confidence": confidence, "approach": approach,
            "intent_match": intent_match, "token_count": token_count})
        resp.raise_for_status()
        return resp.json()

    async def endorse(self, thread_id: str, contribution_id: str, note: str | None = None) -> dict:
        resp = await self._request("POST", f"/internal/threads/{thread_id}/endorse",
                                   json={"target_contribution_id": contribution_id, "note": note})
        resp.raise_for_status()
        return resp.json()

    async def conclude(self, thread_id: str, winning_contribution_id: str,
                       conclusion_type: str, note: str | None = None) -> dict:
        resp = await self._request("POST", f"/internal/threads/{thread_id}/conclude", json={
            "winning_contribution_id": winning_contribution_id,
            "conclusion_type": conclusion_type, "coordinator_note": note})
        resp.raise_for_status()
        return resp.json()
