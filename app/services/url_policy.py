"""URL policy for the structural moderation pre-check.

Deny always wins: a blocklisted host is rejected regardless of the toggle. The
toggle only decides whether an explicit allowlist entry is ALSO required.

An allowlist is a real security control. A BLOCKLIST IS NOT — it is bypassed by
IP literals, shorteners, redirects, and punycode lookalikes. It is here for
policy ("don't paste prod admin panel links"), not for stopping a hostile agent.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Fenced code is exempt — stripped before any URL is extracted.
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Match the AUTHORITY only (scheme + host + optional port), not the whole URL.
# The host is the only thing this module uses, and stopping at the first '/',
# '?' or '#' avoids every trailing-punctuation and bracket-in-path problem.
#
# Two alternatives: a bracketed IPv6 literal, or a run of non-delimiter chars.
# The IPv6 branch is REQUIRED - a single character class cannot both exclude ']'
# (so "](" markdown doesn't get swallowed) and include it (so "[::1]" survives).
# Getting this wrong silently drops every IPv6 URL, which reads as "not a URL"
# and lets it past the blocklist entirely.
_URL_RE = re.compile(
    r"https?://(?:\[[0-9A-Fa-f:.]+\]|[^\s/?#<>\"'`\[\]()\{\},;]+)(?::\d+)?",
    re.IGNORECASE,
)

# The `private` keyword - exactly the ranges the spec defines. Written as CIDR
# so 172.16/12 cannot be got wrong: hand-written "172.*" wrongly includes the
# PUBLIC 172.32-172.255 space.
#
# Link-local (169.254/16, fe80::/10) is deliberately NOT here. 169.254.169.254
# is the cloud metadata endpoint, and a keyword whose whole purpose is "you will
# get these ranges wrong by hand" must not quietly admit it.
_PRIVATE_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "::1/128",
    "fc00::/7",
)

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class UrlPolicyConfigError(ValueError):
    """An ambiguous or malformed list entry. Raised at parse time so a bad
    security list fails the boot instead of being silently reinterpreted."""


@dataclass(frozen=True)
class HostList:
    hostnames: tuple[str, ...] = ()
    networks: tuple[_IPNetwork, ...] = ()

    def matches(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return any(
                host == name or host.endswith("." + name) for name in self.hostnames
            )
        return any(ip in net for net in self.networks)


def _octet_wildcard_to_network(entry: str) -> _IPNetwork:
    octets = entry[:-2].split(".")
    if not 1 <= len(octets) <= 3:
        raise UrlPolicyConfigError(
            f"{entry!r}: octet wildcards take 1-3 leading octets, e.g. '10.*', "
            "'10.1.*', '10.1.2.*'"
        )
    for o in octets:
        if not o.isdigit() or not 0 <= int(o) <= 255:
            raise UrlPolicyConfigError(f"{entry!r}: {o!r} is not a valid octet")
    padded = octets + ["0"] * (4 - len(octets))
    return ipaddress.ip_network(f"{'.'.join(padded)}/{len(octets) * 8}", strict=False)


def parse_host_list(raw: str) -> HostList:
    """Parse a comma-separated list of hosts, IPs, CIDRs, octet wildcards, and
    the `private` keyword. Raises UrlPolicyConfigError on an ambiguous entry."""
    hostnames: list[str] = []
    networks: list[_IPNetwork] = []

    for chunk in (raw or "").split(","):
        # strip(".") not rstrip(".") — a leading-dot entry like '.example.com'
        # is a natural thing to write, and silently matching nothing would fail
        # OPEN on the blocklist.
        entry = chunk.strip().lower().strip(".")
        if not entry:
            continue

        if entry == "private":
            networks.extend(ipaddress.ip_network(n) for n in _PRIVATE_NETWORKS)
            continue

        if entry.startswith("*."):
            hostnames.append(entry[2:])
            continue

        if entry.startswith("*"):
            raise UrlPolicyConfigError(
                f"{entry!r}: a leading '*' with no dot is ambiguous and would also "
                f"match 'not{entry[1:]}'. Write '*.{entry[1:]}' to mean the domain "
                "and its subdomains."
            )

        if entry.endswith(".*"):
            networks.append(_octet_wildcard_to_network(entry))
            continue

        if "/" in entry:
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError as exc:
                raise UrlPolicyConfigError(f"{entry!r}: not a valid CIDR ({exc})") from exc
            continue

        try:
            addr = ipaddress.ip_address(entry)
        except ValueError:
            # A port in a hostname entry never matches (urlparse strips ports
            # from .hostname), so accepting it would fail OPEN on the blocklist.
            if ":" in entry:
                raise UrlPolicyConfigError(
                    f"{entry!r}: hostname entries must not contain ':' — ports are "
                    "ignored when matching, so write just the host"
                )
            hostnames.append(entry)
        else:
            networks.append(ipaddress.ip_network(addr))

    return HostList(tuple(hostnames), tuple(networks))


def _host_of(authority: str) -> str | None:
    """The real host, via urlparse().hostname.

    .hostname NOT .netloc: 'http://trusted.com@evil.com' has a netloc of
    'trusted.com@evil.com' but a hostname of 'evil.com'. Substring matching on
    the raw URL is never acceptable here.
    """
    try:
        host = urlparse(authority).hostname
    except ValueError:
        return None
    # Trailing dot: 'example.com.' is the same host as 'example.com'.
    return host.lower().rstrip(".") if host else None


@dataclass(frozen=True)
class UrlPolicy:
    check_enabled: bool
    allowlist: HostList
    blocklist: HostList

    def find_violation(self, text: str) -> str | None:
        """Return 'url_blocked', 'url_not_permitted', or None."""
        stripped = _CODE_FENCE_RE.sub("", text or "")
        hosts = [_host_of(u) for u in _URL_RE.findall(stripped)]
        if not hosts:
            return None

        # Deny pass first, over every URL — deny always wins.
        for host in hosts:
            if host is not None and self.blocklist.matches(host):
                return "url_blocked"

        if not self.check_enabled:
            return None

        for host in hosts:
            if host is None or not self.allowlist.matches(host):
                return "url_not_permitted"
        return None


def build_policy(settings) -> UrlPolicy:
    """Build from settings. Raises UrlPolicyConfigError on bad config."""
    return UrlPolicy(
        check_enabled=settings.structural_url_check_enabled,
        allowlist=parse_host_list(settings.url_allowlist),
        blocklist=parse_host_list(settings.url_blocklist),
    )
