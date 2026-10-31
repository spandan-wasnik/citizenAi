"""Pure URL assessment: is this link plausibly official, and are there red flags?

An assessment is never a verdict that a link is safe. It only reports:
  * domain_class: 'gov_in', 'nic_in', 'unrecognized', or 'suspicious';
  * danger flags (strong red flags: the link is classed suspicious and is never fetched);
  * warnings (weak signals shown to a reviewer).
A .gov.in / .nic.in host is a positive signal about who registered the domain, not proof
that a particular page is genuine.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domain.domains import classify_domain, host_of

URL_SHORTENERS = frozenset({
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "cutt.ly", "is.gd", "rb.gy", "ow.ly", "buff.ly",
    "shorturl.at", "tiny.cc", "t.ly", "rebrand.ly", "lnkd.in", "wa.me",
})
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_GOV_TOKENS = frozenset({"gov", "govt", "nic"})


@dataclass(frozen=True)
class UrlAssessment:
    url: str
    host: str
    domain_class: str
    danger: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def suspicious(self) -> bool:
        return bool(self.danger)


def _is_ip_or_local(host: str) -> bool:
    if host.isdigit() or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    """Levenshtein distance, stopping early once it exceeds ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def assess_url(url: str, known_domains: tuple[str, ...] | list[str] | frozenset[str] = ()) -> UrlAssessment:
    """Assess a URL. ``known_domains`` are registry domains used for look-alike detection."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return UrlAssessment(url, "", "suspicious", danger=("malformed_url",))

    danger: list[str] = []
    warnings: list[str] = []
    host = host_of(url)

    if parts.scheme not in ("http", "https"):
        danger.append("unsupported_scheme")
    if not host:
        danger.append("no_host")
        return UrlAssessment(url, host, "suspicious", tuple(danger))
    if parts.username is not None or parts.password is not None:
        danger.append("userinfo_in_url")
    if _is_ip_or_local(host):
        danger.append("ip_or_local_host")
    if any(label.startswith("xn--") for label in host.split(".")):
        danger.append("punycode_host")
    if host in URL_SHORTENERS:
        danger.append("url_shortener")

    if parts.scheme == "http":
        warnings.append("not_https")
    if port not in (None, 80, 443):
        warnings.append("non_standard_port")

    domain_class = classify_domain(host)
    if domain_class == "unrecognized":
        warnings.append("domain_not_gov_in_or_nic_in")
        tokens = set(re.split(r"[.\-_]", host))
        if tokens & _GOV_TOKENS or any(f".{s}." in f".{host}." for s in ("gov.in", "nic.in")):
            danger.append("impersonates_gov_domain")
        elif "gov" in host:
            warnings.append("contains_gov_text")
        for known in known_domains:
            known = known.lower()
            if host == known or host.endswith("." + known):
                continue
            brand = known.split(".")[0]  # e.g. 'pmkisan' from 'pmkisan.gov.in'
            reuses_brand = len(brand) >= 6 and any(edit_distance(tok, brand, 1) <= 1 for tok in tokens if len(tok) >= 6)
            if reuses_brand or (len(known) >= 8 and edit_distance(host, known, 2) <= 2):
                danger.append(f"lookalike_of:{known}")

    return UrlAssessment(url, host, "suspicious" if danger else domain_class, tuple(danger), tuple(warnings))
