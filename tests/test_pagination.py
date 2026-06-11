"""Unit tests for cursor-based pagination helper."""
import pytest
from app.pagination import decode_cursor, encode_cursor, build_cursor_clause


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
