from __future__ import annotations
import base64
import json


def encode_cursor(id: str, sort_val) -> str:
    data = json.dumps({"id": id, "sort_val": sort_val})
    return base64.urlsafe_b64encode(data.encode()).decode()


def decode_cursor(cursor: str) -> dict:
    try:
        data = base64.urlsafe_b64decode(cursor.encode()).decode()
        return json.loads(data)
    except Exception:
        raise ValueError("Invalid cursor")


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
    """
    if not cursor:
        return "", params

    decoded = decode_cursor(cursor)
    base = len(params) + 1
    op = "<" if order == "DESC" else ">"
    params = list(params) + [decoded["sort_val"], decoded["id"]]
    clause = (
        f"AND ({sort_col} {op} ${base} "
        f"OR ({sort_col} = ${base} AND id {op} ${base + 1}))"
    )
    return clause, params


def has_more_and_strip(rows: list, limit: int) -> tuple[list, bool]:
    """Fetch limit+1 rows; return (rows[:limit], has_more)."""
    more = len(rows) > limit
    return rows[:limit], more
