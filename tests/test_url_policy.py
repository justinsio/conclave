"""URL policy: host/IP list parsing and violation detection. Pure logic, no DB."""
import pytest

from app.services.url_policy import (
    UrlPolicy,
    UrlPolicyConfigError,
    parse_host_list,
)


def _policy(*, enabled, allow="", block=""):
    return UrlPolicy(
        check_enabled=enabled,
        allowlist=parse_host_list(allow),
        blocklist=parse_host_list(block),
    )


# ── hostname matching ────────────────────────────────────────────────────────

def test_bare_hostname_matches_itself_and_subdomains():
    p = _policy(enabled=True, allow="example.com")
    assert p.find_violation("see https://example.com/x") is None
    assert p.find_violation("see https://wiki.example.com/x") is None
    assert p.find_violation("see https://a.b.example.com/x") is None


def test_hostname_does_not_match_across_label_boundary():
    p = _policy(enabled=True, allow="example.com")
    assert p.find_violation("see https://notexample.com/x") == "url_not_permitted"
    assert p.find_violation("see https://example.com.evil.net/x") == "url_not_permitted"


def test_star_dot_prefix_is_equivalent_to_bare_hostname():
    p = _policy(enabled=True, allow="*.example.com")
    assert p.find_violation("see https://wiki.example.com/x") is None
    assert p.find_violation("see https://notexample.com/x") == "url_not_permitted"


def test_star_without_dot_is_rejected_at_parse_time():
    with pytest.raises(UrlPolicyConfigError) as exc:
        parse_host_list("*example.com")
    assert "*.example.com" in str(exc.value)


# ── the userinfo trap ────────────────────────────────────────────────────────

def test_userinfo_host_resolves_to_the_real_host_not_the_prefix():
    p = _policy(enabled=True, allow="trusted.com")
    assert p.find_violation("http://trusted.com@evil.com/x") == "url_not_permitted"


def test_userinfo_host_is_blocked_when_real_host_is_blocklisted():
    p = _policy(enabled=False, block="evil.com")
    assert p.find_violation("http://trusted.com@evil.com/x") == "url_blocked"


# ── IP matching ──────────────────────────────────────────────────────────────

def test_cidr_entry_matches_addresses_inside_it():
    p = _policy(enabled=True, allow="10.0.0.0/8")
    assert p.find_violation("http://10.1.2.3/x") is None
    assert p.find_violation("http://11.1.2.3/x") == "url_not_permitted"


def test_octet_wildcards_map_to_the_right_prefix_lengths():
    assert _policy(enabled=True, allow="10.*").find_violation("http://10.9.9.9/") is None
    assert _policy(enabled=True, allow="10.1.*").find_violation("http://10.1.9.9/") is None
    assert _policy(enabled=True, allow="10.1.*").find_violation("http://10.2.0.1/") == "url_not_permitted"
    assert _policy(enabled=True, allow="10.1.2.*").find_violation("http://10.1.2.9/") is None
    assert _policy(enabled=True, allow="10.1.2.*").find_violation("http://10.1.3.9/") == "url_not_permitted"


def test_bare_ip_entry_matches_only_that_address():
    p = _policy(enabled=True, allow="10.1.2.3")
    assert p.find_violation("http://10.1.2.3/x") is None
    assert p.find_violation("http://10.1.2.4/x") == "url_not_permitted"


def test_private_keyword_covers_rfc1918_and_loopback_but_not_public():
    p = _policy(enabled=True, allow="private")
    for host in ("10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.1.1", "127.0.0.1"):
        assert p.find_violation(f"http://{host}/x") is None, host
    # The classic mistake this keyword exists to prevent: 172.32+ is PUBLIC.
    for host in ("172.32.0.1", "8.8.8.8", "172.15.0.1"):
        assert p.find_violation(f"http://{host}/x") == "url_not_permitted", host


def test_ipv6_loopback_is_private():
    p = _policy(enabled=True, allow="private")
    assert p.find_violation("http://[::1]:8080/x") is None
    assert p.find_violation("http://[::1]/x") is None


def test_ipv6_literals_are_reachable_by_the_blocklist():
    """Regression: a URL extractor that drops bracketed IPv6 makes every such
    URL invisible, which reads as 'no URL here' and evades the blocklist."""
    p = _policy(enabled=False, block="::1")
    assert p.find_violation("http://[::1]/x") == "url_blocked"


def test_public_ipv6_is_not_private():
    p = _policy(enabled=True, allow="private")
    assert p.find_violation("http://[2001:4860:4860::8888]/x") == "url_not_permitted"


def test_ipv6_cidr_entries_parse():
    parse_host_list("fc00::/7,::1")  # must not raise


def test_link_local_is_not_private():
    """169.254.169.254 is the cloud metadata endpoint. A keyword that exists
    because 'you will get these ranges wrong by hand' must not admit it."""
    p = _policy(enabled=True, allow="private")
    assert p.find_violation(
        "http://169.254.169.254/latest/meta-data/"
    ) == "url_not_permitted"


def test_malformed_octet_wildcard_is_rejected_at_parse_time():
    with pytest.raises(UrlPolicyConfigError):
        parse_host_list("10.999.*")


def test_leading_dot_entry_is_accepted_and_matches():
    """Silently matching nothing would fail OPEN on the blocklist."""
    p = _policy(enabled=True, allow=".example.com")
    assert p.find_violation("https://wiki.example.com/x") is None


def test_hostname_entry_with_a_port_is_rejected_at_parse_time():
    with pytest.raises(UrlPolicyConfigError) as exc:
        parse_host_list("example.com:8080")
    assert "':'" in str(exc.value)


# ── deny always wins ─────────────────────────────────────────────────────────

def test_blocklist_applies_even_when_check_is_disabled():
    p = _policy(enabled=False, block="evil.com")
    assert p.find_violation("see https://evil.com/x") == "url_blocked"
    assert p.find_violation("see https://fine.com/x") is None


def test_blocklist_beats_allowlist_when_check_is_enabled():
    p = _policy(enabled=True, allow="private", block="10.0.0.5")
    assert p.find_violation("http://10.0.0.9/x") is None
    assert p.find_violation("http://10.0.0.5/x") == "url_blocked"


def test_block_wins_over_allow_violation_across_multiple_urls():
    p = _policy(enabled=True, allow="ok.com", block="evil.com")
    text = "first https://nope.com/a then https://evil.com/b"
    assert p.find_violation(text) == "url_blocked"


# ── toggle + fences ──────────────────────────────────────────────────────────

def test_disabled_check_with_empty_blocklist_permits_everything():
    p = _policy(enabled=False)
    assert p.find_violation("https://anything.example/x") is None


def test_enabled_check_with_empty_allowlist_rejects_every_url():
    p = _policy(enabled=True)
    assert p.find_violation("https://anything.example/x") == "url_not_permitted"


def test_urls_inside_code_fences_are_ignored():
    p = _policy(enabled=True)
    text = "here is code:\n```\ncurl https://example.com/api\n```\nthat's all"
    assert p.find_violation(text) is None


def test_text_with_no_urls_never_violates():
    p = _policy(enabled=True)
    assert p.find_violation("no links here at all") is None
    assert p.find_violation("") is None


def test_trailing_punctuation_is_not_part_of_the_host():
    p = _policy(enabled=True, allow="example.com")
    assert p.find_violation("go to https://example.com/x, then stop") is None
    assert p.find_violation("visit https://example.com.") is None
    assert p.find_violation("[link](https://example.com)") is None
    assert p.find_violation("<https://example.com>") is None


def test_explicit_port_does_not_break_host_matching():
    p = _policy(enabled=True, allow="example.com")
    assert p.find_violation("https://example.com:8443/x") is None


# ── list parsing hygiene ─────────────────────────────────────────────────────

def test_list_parsing_is_case_insensitive_and_ignores_blanks():
    p = _policy(enabled=True, allow="  EXAMPLE.com ,, ")
    assert p.find_violation("https://example.com/x") is None


def test_uppercase_host_in_text_matches_lowercase_entry():
    p = _policy(enabled=True, allow="example.com")
    assert p.find_violation("https://EXAMPLE.COM/x") is None
