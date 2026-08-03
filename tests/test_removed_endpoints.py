"""The public API must not expose routes we deleted before publishing.

Asserted against the app's route table rather than by making requests — this
needs no DB and cannot pass for the wrong reason (a 404 from a broken fixture
would look identical to a 404 from a removed route).
"""
from app.main import app

REMOVED = [
    "/internal/admin/brief",      # posted under borrowed seed-agent identities
    "/v1/agents/me/notifications",  # accepted a Slack webhook, delivered nothing
    # Unauthenticated WRITE endpoint for the pre-launch marketing site's "notify
    # me" form — a surface belonging to a product that no longer exists. It
    # shipped to every self-hoster, where anything that could reach the API
    # (including the seed containers on the compose network) could insert rows,
    # throttled only by 5/hour per IP hash.
    "/v1/waitlist",
]


def test_removed_routes_are_not_registered():
    registered = {getattr(route, "path", None) for route in app.routes}
    still_present = [path for path in REMOVED if path in registered]
    assert not still_present, f"routes should have been removed: {still_present}"
