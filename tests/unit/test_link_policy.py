import pytest

from app.domain.link_policy import assess_url, edit_distance

REGISTRY = ("pmkisan.gov.in", "scholarships.gov.in", "mahadbt.maharashtra.gov.in", "myscheme.gov.in")


@pytest.mark.parametrize("url,cls", [
    ("https://pmkisan.gov.in/", "gov_in"),
    ("https://www.india.gov.in/x?y=1", "gov_in"),
    ("https://nsap.nic.in", "nic_in"),
    ("https://mudra.org.in", "unrecognized"),
    ("https://standupmitra.in", "unrecognized"),
])
def test_ordinary_links_are_classified_without_danger(url, cls):
    a = assess_url(url, REGISTRY)
    assert a.domain_class == cls and not a.suspicious and a.danger == ()


@pytest.mark.parametrize("url,flag", [
    ("https://pmkisan.gov.in@evil.com/", "userinfo_in_url"),
    ("https://user:pw@pmkisan.gov.in/", "userinfo_in_url"),
    ("http://192.168.1.10/admin", "ip_or_local_host"),
    ("http://2130706433/", "ip_or_local_host"),
    ("http://[::1]/", "ip_or_local_host"),
    ("http://localhost:8000/", "ip_or_local_host"),
    ("https://intranet.internal/x", "ip_or_local_host"),
    ("https://xn--pmkisn-5ya.gov.in/", "punycode_host"),
    ("https://xn--80ak6aa92e.com/", "punycode_host"),
    ("https://bit.ly/abc", "url_shortener"),
    ("ftp://pmkisan.gov.in/file", "unsupported_scheme"),
    ("javascript:alert(1)", "unsupported_scheme"),
    ("https://gov.in.evil.com/", "impersonates_gov_domain"),
    ("https://pmkisan-gov-in.com/", "impersonates_gov_domain"),
    ("https://pmkisan.govt.example.com/", "impersonates_gov_domain"),
    ("https://nic.in.attacker.org/", "impersonates_gov_domain"),
    ("https://pmkisan.org.in/", "lookalike_of:pmkisan.gov.in"),
    ("https://scholarship.gov.example.com/", "impersonates_gov_domain"),
])
def test_red_flags_make_a_link_suspicious(url, flag):
    a = assess_url(url, REGISTRY)
    assert a.suspicious and a.domain_class == "suspicious"
    assert any(flag == d or d.startswith(flag) for d in a.danger), a.danger


def test_lookalike_of_registry_domain_is_caught_but_registry_subdomains_are_not():
    assert assess_url("https://mahadbt-maharashtra.in", REGISTRY).suspicious
    assert assess_url("https://scholarshlps.gov.in/", REGISTRY).domain_class == "gov_in"  # gov.in is government-run
    assert not assess_url("https://pmkisan.gov.in/", REGISTRY).suspicious  # the domain itself is fine


def test_weak_signals_are_warnings_not_dangers():
    a = assess_url("http://example.org:8080/x", REGISTRY)
    assert not a.suspicious
    assert {"not_https", "non_standard_port", "domain_not_gov_in_or_nic_in"} <= set(a.warnings)
    b = assess_url("https://evilgov.in/", REGISTRY)
    assert not b.suspicious and "contains_gov_text" in b.warnings


@pytest.mark.parametrize("bad", ["", "   ", "not a url", "https://", "http://[bad"])
def test_malformed_urls_are_suspicious_not_crashes(bad):
    a = assess_url(bad, REGISTRY)
    assert a.suspicious and a.domain_class == "suspicious"


def test_known_domains_are_optional():
    assert not assess_url("https://pmkisan.org.in/").suspicious  # no registry given, no look-alike check


def test_edit_distance():
    assert edit_distance("abc", "abc") == 0
    assert edit_distance("abc", "abd") == 1
    assert edit_distance("kitten", "sitting", limit=5) == 3
    assert edit_distance("a", "abcdefgh", limit=2) == 3  # early exit past the limit
