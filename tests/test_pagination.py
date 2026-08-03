"""Unit tests for cursor-based pagination helper."""
import base64
import json

import pytest
from fastapi import HTTPException

from app.pagination import decode_cursor, encode_cursor, build_cursor_clause


def _b64(obj) -> str:
    """A syntactically valid cursor carrying arbitrary JSON."""
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()


# A malformed cursor is CLIENT input. Every one of these used to reach the route
# as an unhandled exception and surface as HTTP 500 — the server reporting its
# own failure for someone else's typo. The first case is the subtle one: the
# decode is wrapped in try/except and raises a tidy ValueError("Invalid cursor"),
# which looks handled and was not caught anywhere.
MALFORMED = [
    pytest.param("not-base64!!!", id="not-base64"),
    pytest.param(base64.urlsafe_b64encode(b"hello").decode(), id="valid-b64-not-json"),
    pytest.param(_b64(None), id="json-null"),
    pytest.param(_b64([1, 2]), id="json-list"),
    pytest.param(_b64("a string"), id="json-string"),
    pytest.param(_b64({"foo": "bar"}), id="dict-missing-both-keys"),
    pytest.param(_b64({"id": "x"}), id="dict-missing-sort_val"),
    pytest.param(_b64({"sort_val": "x"}), id="dict-missing-id"),
]


def test_encode_decode_roundtrip():
    cursor = encode_cursor("abc-123", "2026-06-10T00:00:00+00:00")
    decoded = decode_cursor(cursor)
    assert decoded["id"] == "abc-123"
    assert decoded["sort_val"] == "2026-06-10T00:00:00+00:00"


def test_decode_invalid_cursor_raises():
    with pytest.raises(ValueError, match="Invalid cursor"):
        decode_cursor("not-base64!!!")


def test_encode_decode_numeric_sort_val():
    cursor = encode_cursor("abc-123", 42)
    decoded = decode_cursor(cursor)
    assert decoded["id"] == "abc-123"
    assert decoded["sort_val"] == 42


def test_build_cursor_clause_no_cursor():
    clause, params = build_cursor_clause(None, [], sort_col="created_at", order="DESC")
    assert clause == ""
    assert params == []


def test_build_cursor_clause_with_cursor():
    cursor = encode_cursor("abc-123", "2026-06-10T00:00:00+00:00")
    clause, params = build_cursor_clause(cursor, [], sort_col="created_at", order="DESC")
    assert "$1" in clause
    assert "$2" in clause
    assert len(params) == 2
    assert params[0] == "2026-06-10T00:00:00+00:00"
    assert params[1] == "abc-123"


# ─── Malformed cursors are a 400, not a 500 ──────────────────────────────────
# decode_cursor stays framework-free and keeps raising ValueError (tested above).
# build_cursor_clause is the single funnel every paginated route goes through,
# so the HTTP translation lives there — one place, all callers.


@pytest.mark.parametrize("cursor", MALFORMED)
def test_malformed_cursor_raises_400_not_an_unhandled_error(cursor):
    with pytest.raises(HTTPException) as exc:
        build_cursor_clause(cursor, [], sort_col="created_at", order="DESC")
    assert exc.value.status_code == 400


def test_the_400_names_the_problem():
    """A bare 500 tells a client nothing. It must be able to see that the cursor
    is what is wrong, without guessing."""
    with pytest.raises(HTTPException) as exc:
        build_cursor_clause("not-base64!!!", [], sort_col="created_at", order="DESC")
    body = str(exc.value.detail).lower()
    assert "cursor" in body


# ─── The sort value must survive its own round trip ──────────────────────────
# It did not. Every call site stringified it, so the follow-up query bound a str
# against a TIMESTAMPTZ or INTEGER column, asyncpg raised DataError, and every
# "next page" request returned 500. The bug was invisible to the old unit tests
# because they never touched a database, and to the API tests because none of
# them ever followed a next_cursor.


def test_a_datetime_sort_value_comes_back_as_a_datetime():
    from datetime import datetime, timezone

    ts = datetime(2026, 6, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)
    cursor = encode_cursor("abc-123", ts)
    _, params = build_cursor_clause(cursor, [], sort_col="created_at")
    assert params[0] == ts, "asyncpg cannot bind a str to a TIMESTAMPTZ column"
    assert isinstance(params[0], datetime)


def test_an_int_sort_value_comes_back_as_an_int():
    cursor = encode_cursor("abc-123", 42)
    _, params = build_cursor_clause(cursor, [], sort_col="upvote_count")
    assert params[0] == 42
    assert isinstance(params[0], int) and not isinstance(params[0], bool)


def test_an_untagged_legacy_cursor_still_decodes():
    """A cursor minted before the type tag existed must not become a hard error —
    it decodes as the raw value, which is exactly the old behaviour."""
    legacy = base64.urlsafe_b64encode(
        json.dumps({"id": "abc-123", "sort_val": "2026-06-10T00:00:00+00:00"}).encode()
    ).decode()
    _, params = build_cursor_clause(legacy, [], sort_col="created_at")
    assert params[0] == "2026-06-10T00:00:00+00:00"


def test_a_corrupt_timestamp_in_a_tagged_cursor_is_a_400_not_a_crash():
    """The type tag is client-controlled too — claiming 'dt' over junk must not
    escape as an unhandled ValueError from datetime.fromisoformat."""
    bad = base64.urlsafe_b64encode(
        json.dumps({"id": "x", "sort_val": "not-a-timestamp", "t": "dt"}).encode()
    ).decode()
    with pytest.raises(HTTPException) as exc:
        build_cursor_clause(bad, [], sort_col="created_at")
    assert exc.value.status_code == 400


def test_a_valid_cursor_is_untouched_by_the_guard():
    """The guard must not start rejecting cursors the API itself issued."""
    cursor = encode_cursor("abc-123", 42)
    clause, params = build_cursor_clause(cursor, ["existing"], sort_col="created_at")
    assert params == ["existing", 42, "abc-123"]
    assert clause.startswith("AND (")
