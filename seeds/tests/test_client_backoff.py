import asyncio
import httpx
from client import ConclaveClient
from tests.conftest import mock_client


async def test_retries_on_429_then_succeeds(config, monkeypatch):
    async def _noop(*_a, **_k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _noop)
    state = {"n": 0}
    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"version": "1.0", "rules": [], "published_at": "x", "changelog": []})
    c = ConclaveClient(config, http=mock_client(handler))
    resp = await c._request("GET", "/v1/rules")
    assert resp.status_code == 200 and state["n"] == 2
