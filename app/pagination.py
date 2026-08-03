from __future__ import annotations
import base64
import json
from datetime import date, datetime

from fastapi import HTTPException

# Cursors are opaque to clients but they are CLIENT INPUT on the way back in:
# anything can be sent in ?cursor=. Two rules follow, and both were broken.
#
#   1. A malformed cursor is a 400, never a 500. It used to be a 500 on every
#      shape — including the one decode_cursor explicitly guards, because the
#      tidy ValueError it raises was caught by nobody. A server reporting its own
#      failure for a client's typo also buries real incidents: any authenticated
#      agent could generate 500s at will.
#
#   2. A cursor this API issued must work when handed back. It did not. All three
#      call sites stringified the sort value (.isoformat() / str()), and the
#      comparison then bound that string against a TIMESTAMPTZ or INTEGER column,
#      so asyncpg raised DataError and pagination past page 1 failed with a 500
#      on every endpoint. JSON has no datetime, so the type must travel WITH the
#      value — hence the "t" tag below — and callers now pass the raw value.

_TYPE_KEY = "t"
_T_DATETIME = "dt"
_T_DATE = "d"


def encode_cursor(id: str, sort_val) -> str:
    """Build an opaque cursor that round-trips its sort value's TYPE.

    Pass the raw column value — a datetime, an int — not a stringified one.
    """
    if isinstance(sort_val, datetime):
        payload = {"id": id, "sort_val": sort_val.isoformat(), _TYPE_KEY: _T_DATETIME}
    elif isinstance(sort_val, date):
        payload = {"id": id, "sort_val": sort_val.isoformat(), _TYPE_KEY: _T_DATE}
    else:
        # int, float, str — JSON preserves these, and asyncpg binds them directly.
        payload = {"id": id, "sort_val": sort_val}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_cursor(cursor: str) -> dict:
    """Decode to the raw payload dict.

    Deliberately framework-free and unchanged in contract: it still raises
    ValueError. The HTTP translation belongs in build_cursor_clause, which is the
    single funnel every paginated route goes through.
    """
    try:
        data = base64.urlsafe_b64decode(cursor.encode()).decode()
        return json.loads(data)
    except Exception:
        raise ValueError("Invalid cursor")


def _restore(payload: dict):
    """Rebuild the sort value's original type from the tag encode_cursor wrote.

    An untagged payload is returned as-is — that is what a cursor minted before
    this existed looks like, and passing it through preserves the old behaviour
    rather than turning an old cursor into a hard error.
    """
    value = payload["sort_val"]
    tag = payload.get(_TYPE_KEY)
    if tag == _T_DATETIME:
        return datetime.fromisoformat(value)
    if tag == _T_DATE:
        return date.fromisoformat(value)
    return value


def build_cursor_clause(
    cursor: str | None,
    params: list,
    sort_col: str = "created_at",
    order: str = "DESC",
) -> tuple[str, list]:
    """Return (WHERE clause fragment, updated params list).

    Caller builds full query:
        WHERE <other conditions> {clause}
        ORDER BY {sort_col} {order}, id {order}
        LIMIT n

    Raises HTTPException(400) on any malformed cursor.
    """
    if not cursor:
        return "", params

    try:
        payload = decode_cursor(cursor)
        sort_val = _restore(payload)
        cursor_id = payload["id"]
    except (ValueError, TypeError, KeyError, AttributeError):
        # ValueError  — not base64, not JSON, or an unparseable timestamp
        # TypeError   — valid JSON of the wrong type (null, a list, a bare string)
        # KeyError    — a dict without "id" / "sort_val"
        # AttributeError — a non-dict that survives the above
        # `from None`: the client error is fully described, and the chained
        # traceback only adds noise to the log.
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_cursor",
                "message": "Invalid cursor. Use the next_cursor value returned "
                           "by a previous page, unmodified, or omit it to start "
                           "from the beginning.",
            },
        ) from None

    base = len(params) + 1
    op = "<" if order == "DESC" else ">"
    params = list(params) + [sort_val, cursor_id]
    clause = (
        f"AND ({sort_col} {op} ${base} "
        f"OR ({sort_col} = ${base} AND id {op} ${base + 1}))"
    )
    return clause, params


def has_more_and_strip(rows: list, limit: int) -> tuple[list, bool]:
    """Fetch limit+1 rows; return (rows[:limit], has_more)."""
    more = len(rows) > limit
    return rows[:limit], more
