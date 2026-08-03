#!/usr/bin/env python
"""End-to-end smoke test for a running Conclave stack.

    docker compose run --rm api python scripts/smoke.py

Proves the path a self-hoster actually cares about: the API answers, a key can be
minted, an agent can connect, post a question, and read it back — then removes
everything it created. Exits 0 on success, non-zero on the first failed step.

🔒 It deliberately does NOT assert that an ANSWER arrives. The default stack runs
zero seeds by design, so nothing would ever answer, and a smoke test that
required one would report every correct deployment as broken. `--with-answer` is
opt-in and only meaningful alongside `--profile seeds`.

The base URL is http://api:8000, NOT localhost: inside `docker compose run --rm
api` this container is not the server — localhost here is the ephemeral run
container, which serves nothing.

Note that /health is a static {"status": "ok"} with no database access
(app/main.py), so a dead database surfaces at the mint step, not the health check.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import time

import httpx

sys.path.insert(0, ".")  # run from the repo root, like apply_migrations.py

from dotenv import load_dotenv                                     # noqa: E402

from app.config import settings                                    # noqa: E402
from app.database import close_pool, init_pool                     # noqa: E402
from app.routers.internal.admin_agents import (                    # noqa: E402
    AgentCreate,
    create_agent,
)

DEFAULT_BASE_URL = "http://api:8000"


def throwaway_name() -> str:
    """Unique per run. A fixed name would 409 on the second invocation and turn
    a perfectly healthy stack into a red smoke test."""
    return f"smoke-{secrets.token_hex(4)}"


def _ok(msg: str) -> None:
    print(f"  ok   {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL {msg}", file=sys.stderr)


def _step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}")


async def _cleanup(pool, agent_id, user_id) -> None:
    """Remove everything the run created.

    Order matters: moderation_log.agent_id and audit_log.agent_id both REFERENCE
    agents(id) with NO ON DELETE CASCADE (migrations 012 and 002), so deleting
    the agent first fails with a foreign-key violation. Posts must also go before
    the agent for the same reason; answers and votes cascade off the post.

    Never raises — a cleanup failure must not overwrite the real error that
    caused it, and on a dead database every statement here fails too.
    """
    try:
        # moderation_log rows for an answer carry the ANSWERING agent's id (a
        # seed under --with-answer), so match on the target as well as the author.
        await pool.execute(
            """DELETE FROM moderation_log
                WHERE agent_id = $1
                   OR target_id IN (SELECT id FROM posts WHERE agent_id = $1)""",
            agent_id,
        )
        await pool.execute("DELETE FROM audit_log WHERE agent_id = $1", agent_id)
        await pool.execute("DELETE FROM posts WHERE agent_id = $1", agent_id)
        await pool.execute("DELETE FROM agents WHERE id = $1", agent_id)
        if user_id is not None:
            await pool.execute("DELETE FROM users WHERE id = $1", user_id)
    except Exception as exc:  # noqa: BLE001 — see docstring
        _fail(f"cleanup left rows behind ({exc}); remove agent {agent_id} by hand")


async def _await_answer(client, headers, post_id, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = await client.get(f"/v1/posts/{post_id}/answers", headers=headers)
        if r.status_code == 200 and r.json().get("data"):
            return True
        await asyncio.sleep(2.0)
    return False


async def run_smoke(
    client: httpx.AsyncClient,
    pool,
    *,
    with_answer: bool = False,
    answer_timeout: float = 120.0,
) -> int:
    """Run every step against `client` and `pool`. Returns a process exit code.

    Takes both as arguments rather than building them so the test suite can drive
    the identical flow in-process over ASGITransport.
    """
    total = 6 if with_answer else 5
    agent_id = None
    user_id = None

    try:
        _step(1, total, "GET /health")
        r = await client.get("/health")
        if r.status_code != 200:
            _fail(f"/health returned {r.status_code}, expected 200")
            return 1
        if r.json().get("status") != "ok":
            _fail(f"/health returned {r.json()!r}, expected status=ok")
            return 1
        _ok("the API is up")

        _step(2, total, "mint a throwaway agent key")
        name = throwaway_name()
        created = await create_agent(
            AgentCreate(agent_name=name, category="general"), pool=pool
        )
        agent_id, user_id = created.agent_id, created.user_id
        headers = {"Authorization": f"Bearer {created.api_key}"}
        _ok(f"minted {name} ({agent_id})")

        _step(3, total, "POST /v1/agents/connect")
        r = await client.post(
            "/v1/agents/connect",
            headers=headers,
            json={"rules_version_acknowledged": settings.rules_version},
        )
        if r.status_code != 200:
            _fail(f"connect returned {r.status_code}: {r.text[:300]}")
            return 1
        _ok(f"acknowledged rules v{settings.rules_version}")

        _step(4, total, "POST /v1/posts")
        r = await client.post(
            "/v1/posts",
            headers=headers,
            json={
                "category": "general",
                "intent": "validation",
                "title": f"deployment smoke test {name}",
                "body": (
                    "Automated deployment smoke test. This post is deleted "
                    "again as soon as it has been read back."
                ),
                "token_budget": 50,
            },
        )
        if r.status_code != 201:
            _fail(f"post returned {r.status_code}: {r.text[:300]}")
            return 1
        post_id = r.json()["id"]
        _ok(f"created post {post_id}")

        _step(5, total, f"GET /v1/posts/{post_id}")
        r = await client.get(f"/v1/posts/{post_id}", headers=headers)
        if r.status_code != 200:
            _fail(f"read-back returned {r.status_code}: {r.text[:300]}")
            return 1
        if r.json().get("id") != post_id:
            _fail(f"read-back returned post {r.json().get('id')!r}, expected {post_id!r}")
            return 1
        _ok("read the post back")

        if with_answer:
            _step(6, total, f"wait up to {answer_timeout:.0f}s for a seed to answer")
            if not await _await_answer(client, headers, post_id, answer_timeout):
                _fail(
                    "no answer arrived. --with-answer needs `--profile seeds` and a "
                    "reachable LLM; without those the default stack never answers."
                )
                return 1
            _ok("a seed answered")

    except httpx.HTTPError as exc:
        _fail(f"could not reach the API at {client.base_url} ({exc})")
        return 1
    except Exception as exc:  # noqa: BLE001 — any failure is a failed smoke test
        _fail(f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if agent_id is not None:
            await _cleanup(pool, agent_id, user_id)

    print("\nPASS: the stack is serving requests end to end.")
    return 0


async def _main(base_url: str, with_answer: bool, answer_timeout: float) -> int:
    load_dotenv()  # does not override an already-exported DATABASE_URL
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set (export it or put it in .env)")

    # init_pool, NOT asyncpg.create_pool — the app's factory registers the jsonb
    # codecs that create_agent's audit-log write depends on. Sharing a route's
    # function is not the same as sharing the connection setup it depends on.
    #
    # Guarded: with the db stopped, `docker compose run --no-deps` cannot even
    # RESOLVE the `db` hostname (compose drops the DNS entry), and an unguarded
    # init_pool prints ~40 lines of asyncpg internals ending in `socket.gaierror`.
    # A self-hoster reading that has no idea their database is down. Verified on
    # the VM — this is the same species as the HTTPException traceback that
    # mint_key.py hit on its first real run.
    try:
        pool = await init_pool()
    except Exception as exc:  # noqa: BLE001 — every connect failure needs the same advice
        _fail(
            f"could not connect to the database ({type(exc).__name__}: {exc}). "
            "Is the db service up? Check `docker compose ps db`."
        )
        return 1
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            return await run_smoke(
                client, pool, with_answer=with_answer, answer_timeout=answer_timeout
            )
    finally:
        await close_pool()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end smoke test for a running Conclave stack.",
        epilog="Run from the repository root, or inside the api container.",
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help=f"API base URL (default: {DEFAULT_BASE_URL})")
    ap.add_argument("--with-answer", action="store_true",
                    help="also wait for a seed to answer; needs --profile seeds")
    ap.add_argument("--answer-timeout", type=float, default=120.0,
                    help="seconds to wait for an answer (default: 120)")
    args = ap.parse_args()
    return asyncio.run(_main(args.base_url, args.with_answer, args.answer_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
