"""Guards on how migration SQL is read off disk.

These do not test application behaviour. They pin an encoding trap that is
invisible on the machines where the project is normally run, and silent — not
loud — on the machines where it is not.

`Path.read_text()` with no `encoding=` decodes using
`locale.getpreferredencoding(False)`. On Linux with a UTF-8 locale that is
UTF-8 and everything is fine, which is why CI and the Docker images never
caught this. On **Windows that is cp1252**, and a UTF-8 file containing an em
dash decodes to mojibake *without raising* — every byte maps to some cp1252
character, so there is no error to notice.

Measured on a Windows host before the fix: 17 of 19 migration files decoded to
something other than their true contents.

Why that was survivable, and why it must not be relied on: all the non-ASCII in
these files sits inside `--` comments, which PostgreSQL discards. The corruption
was real but landed somewhere harmless. The moment a non-ASCII character appears
in executable SQL — a CHECK constraint message, a COMMENT ON, a seeded row — it
corrupts the database instead, on exactly the hosts least likely to be testing.

So there are two guards here, and they are load-bearing in different ways:

  1. `test_no_encodingless_read_text` fixes the cause. Any reader that names its
     encoding is correct on every host regardless of locale.
  2. `test_executable_sql_is_ascii_only` restores the project's long-standing
     "keep migration files ASCII-only" rule, which had drifted to 17 violations
     without anything enforcing it. Guard 1 makes guard 2 unnecessary in theory;
     guard 2 is what keeps a future encoding-less reader from being a data bug
     instead of a cosmetic one.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "migrations"

# Directories whose Python we control. .venv and caches are excluded — third
# party code is not ours to police.
SOURCE_DIRS = ("app", "scripts", "tests", "seeds", "dashboard", "evals")


def _encodingless_read_text_calls(source: str):
    """Yield line numbers of `.read_text(...)` calls with no `encoding=` kwarg.

    Parsed with `ast` rather than matched with a regex. A regex over raw source
    also matches the words `read_text()` inside docstrings and comments — this
    module's own docstring discusses the bug at length and was the first false
    positive. The AST sees calls only.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "read_text"):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        yield node.lineno


def _python_files():
    for d in SOURCE_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_no_encodingless_read_text():
    """Every `read_text()` in our own source must name its encoding.

    This is a source-level lint rather than a behavioural test on purpose: the
    bug only manifests under a non-UTF-8 locale, and a test process cannot
    portably change its own locale to reproduce that. Pinning the call shape is
    what actually prevents a regression.
    """
    offenders = []
    for path in _python_files():
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for lineno in _encodingless_read_text_calls(text):
            snippet = lines[lineno - 1].strip() if lineno <= len(lines) else ""
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {snippet}")

    assert not offenders, (
        "read_text() called without an explicit encoding. On a non-UTF-8 host "
        "(Windows defaults to cp1252) this silently mis-decodes the file rather "
        "than raising. Pass encoding=\"utf-8\".\n  " + "\n  ".join(offenders)
    )


def test_migrations_are_valid_utf8():
    """The files must actually BE UTF-8, or naming utf-8 as the encoding fails."""
    bad = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError as exc:
            bad.append(f"{path.name}: {exc}")
    assert not bad, "migration files are not valid UTF-8:\n  " + "\n  ".join(bad)


def test_executable_sql_is_ascii_only():
    """Non-ASCII is allowed in comments, never in SQL Postgres will execute.

    Comments are discarded by the server, so a mis-decode there is cosmetic. In
    executable SQL the same mis-decode changes a constraint message, a column
    comment, or seeded data — and does so only on some hosts, which is the worst
    possible failure shape.
    """
    offenders = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_bytes().decode("utf-8")
        # Strip block comments first, then line comments.
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        stripped = "\n".join(line.split("--")[0] for line in stripped.splitlines())
        for lineno, line in enumerate(stripped.splitlines(), 1):
            for char in line:
                if ord(char) > 127:
                    offenders.append(
                        f"{path.name}:{lineno}: U+{ord(char):04X} ({char!r}) in executable SQL"
                    )
                    break

    assert not offenders, (
        "non-ASCII characters found in executable migration SQL. Keep them to "
        "comments — see this module's docstring.\n  " + "\n  ".join(offenders)
    )
