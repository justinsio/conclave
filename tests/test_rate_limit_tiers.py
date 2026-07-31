"""Operator-defined rate tiers. Pure logic, no DB."""
import pytest

from app.services.rate_limit import get_rate_limits, parse_rate_limit_tiers


def test_empty_override_yields_the_builtin_defaults():
    assert parse_rate_limit_tiers("") == {}


def test_parses_name_equals_number_pairs():
    assert parse_rate_limit_tiers("contractor=20,gold=200") == {
        "contractor": 20, "gold": 200,
    }


def test_ignores_whitespace_and_blank_entries():
    assert parse_rate_limit_tiers(" contractor = 20 ,, gold=200 ") == {
        "contractor": 20, "gold": 200,
    }


@pytest.mark.parametrize("bad", ["contractor", "contractor=", "contractor=abc", "=20"])
def test_malformed_entries_are_rejected_at_parse_time(bad):
    with pytest.raises(ValueError):
        parse_rate_limit_tiers(bad)


def test_negative_and_zero_limits_are_rejected():
    for bad in ("contractor=0", "contractor=-5"):
        with pytest.raises(ValueError):
            parse_rate_limit_tiers(bad)


def test_tier_name_longer_than_the_column_is_rejected():
    """agents.plan is VARCHAR(20) — a longer name could never be assigned."""
    with pytest.raises(ValueError):
        parse_rate_limit_tiers("a" * 21 + "=20")


def test_override_merges_over_defaults_rather_than_replacing(monkeypatch):
    """The footgun this exists to kill: setting one tier must not wipe 'seed'."""
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_tiers", "reader=30,contractor=20")
    limits = get_rate_limits()
    assert limits["reader"] == 30        # overridden
    assert limits["contractor"] == 20    # added
    assert limits["seed"] == 300         # PRESERVED
    assert limits["admin"] == 1000       # PRESERVED


def test_setitem_on_the_defaults_still_works(monkeypatch):
    """Existing tests monkeypatch.setitem(settings.rate_limits, ...)."""
    from app.config import settings
    monkeypatch.setattr(settings, "rate_limit_tiers", "")
    monkeypatch.setitem(settings.rate_limits, "reader", 3)
    assert get_rate_limits()["reader"] == 3
