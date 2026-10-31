import json

import httpx
import pytest
from sqlalchemy import text

from app.database import models as m
from app.database.connection import make_engine, session_scope
from app.services.source_verifier import LinkChecker, CheckResult, record_check, status_for_outcome, verify_links
from scripts.init_db import init_database

REGISTRY = {
    "pmkisan.gov.in": {"automated_access": "unknown"},
    "myscheme.gov.in": {"automated_access": "prohibited"},
    "other.gov.in": {"automated_access": "unknown"},
}


class Net:
    """Fake network: maps (method, url) or url to a response factory; records every request."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url, request.headers.get("user-agent")))
        for key in ((request.method, url), url):
            if key in self.routes:
                value = self.routes[key]
                if isinstance(value, Exception):
                    raise value
                if callable(value):
                    return value(request)
                return value
        return httpx.Response(404)

    def urls(self, method=None):
        return [u for mth, u, _ in self.requests if method in (None, mth)]


def make_checker(routes, **kw):
    net = Net({"https://pmkisan.gov.in/robots.txt": httpx.Response(404),
               "https://other.gov.in/robots.txt": httpx.Response(404), **routes})
    sleeps = []
    clock = {"t": 1000.0}

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    checker = LinkChecker(httpx.Client(transport=httpx.MockTransport(net)), registry=REGISTRY,
                          sleep=sleep, monotonic=lambda: clock["t"], **kw)
    return checker, net, sleeps


# ------------------------------------------------------------------ outcomes
def test_ok_page_is_checked_with_head_and_identifying_user_agent():
    checker, net, _ = make_checker({"https://pmkisan.gov.in/": httpx.Response(200)})
    r = checker.check("https://pmkisan.gov.in/")
    assert (r.outcome, r.http_status, r.redirect_chain) == ("ok", 200, [])
    assert net.urls("HEAD") == ["https://pmkisan.gov.in/"]
    assert all("CitizenAssistAI" in ua for _, _, ua in net.requests)
    assert r.checked_at


def test_head_refused_falls_back_to_get():
    checker, net, _ = make_checker({("HEAD", "https://pmkisan.gov.in/a"): httpx.Response(405),
                                    ("GET", "https://pmkisan.gov.in/a"): httpx.Response(200)})
    assert checker.check("https://pmkisan.gov.in/a").outcome == "ok"
    assert net.urls("GET")[-1] == "https://pmkisan.gov.in/a"


def test_same_host_redirect_is_recorded_and_followed():
    checker, _, _ = make_checker({
        "https://pmkisan.gov.in/old": httpx.Response(301, headers={"Location": "/new"}),
        "https://pmkisan.gov.in/new": httpx.Response(200)})
    r = checker.check("https://pmkisan.gov.in/old")
    assert r.outcome == "redirected_same_host"
    assert r.redirect_chain == ["https://pmkisan.gov.in/new"] and r.final_url == "https://pmkisan.gov.in/new"


def test_redirect_to_another_host_is_flagged_for_human_review():
    checker, _, _ = make_checker({
        "https://pmkisan.gov.in/x": httpx.Response(302, headers={"Location": "https://other.gov.in/y"}),
        "https://other.gov.in/y": httpx.Response(200)})
    assert checker.check("https://pmkisan.gov.in/x").outcome == "redirected_other_host"


def test_redirect_to_suspicious_destination_is_never_followed():
    checker, net, _ = make_checker({
        "https://pmkisan.gov.in/x": httpx.Response(302, headers={"Location": "http://10.0.0.5/secret"})})
    r = checker.check("https://pmkisan.gov.in/x")
    assert r.outcome == "redirect_to_unsafe" and "ip_or_local_host" in r.detail
    assert all("10.0.0.5" not in u for u in net.urls())


def test_redirect_loop_is_cut_off():
    checker, _, _ = make_checker({"https://pmkisan.gov.in/a": httpx.Response(302, headers={"Location": "/a"})},
                                 max_redirects=3)
    assert checker.check("https://pmkisan.gov.in/a").outcome == "too_many_redirects"


@pytest.mark.parametrize("code,outcome", [(404, "broken"), (410, "broken"), (400, "broken"),
                                          (403, "inconclusive"), (401, "inconclusive"),
                                          (429, "inconclusive"), (500, "inconclusive")])
def test_status_codes_map_to_outcomes(code, outcome):
    checker, _, _ = make_checker({"https://pmkisan.gov.in/p": httpx.Response(code)}, retries=0)
    assert checker.check("https://pmkisan.gov.in/p").outcome == outcome


def test_403_is_not_reported_as_broken():
    """Government sites often refuse bots; that must not mark a good link broken."""
    checker, _, _ = make_checker({"https://pmkisan.gov.in/p": httpx.Response(403)}, retries=0)
    r = checker.check("https://pmkisan.gov.in/p")
    assert status_for_outcome(r.outcome, "verified_manual") == "verified_manual"


# ------------------------------------------------------------------ safety and policy
def test_suspicious_url_is_not_fetched_at_all():
    checker, net, _ = make_checker({})
    r = checker.check("https://pmkisan.gov.in@evil.com/")
    assert r.outcome == "skipped_unsafe" and net.requests == []


def test_registry_prohibition_is_respected_without_any_request():
    checker, net, _ = make_checker({})
    r = checker.check("https://www.myscheme.gov.in/schemes/x")
    assert r.outcome == "skipped_policy" and net.requests == []


def test_robots_disallow_blocks_fetching():
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    checker, net, _ = make_checker({"https://pmkisan.gov.in/robots.txt": robots,
                                    "https://pmkisan.gov.in/private/a": httpx.Response(200)})
    r = checker.check("https://pmkisan.gov.in/private/a")
    assert r.outcome == "skipped_robots"
    assert "https://pmkisan.gov.in/private/a" not in net.urls()
    assert checker.check("https://pmkisan.gov.in/public").outcome != "skipped_robots"


def test_robots_rules_for_our_agent_override_wildcard():
    robots = httpx.Response(200, text="User-agent: CitizenAssistAI\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    checker, _, _ = make_checker({"https://pmkisan.gov.in/robots.txt": robots})
    assert checker.check("https://pmkisan.gov.in/x").outcome == "skipped_robots"


@pytest.mark.parametrize("robots,fetched", [
    (httpx.Response(404), True),                       # no robots.txt: no restrictions
    (httpx.Response(403), True),                       # 4xx: no restrictions
    (httpx.Response(503), False),                      # 5xx: assume disallowed
    (httpx.ConnectError("down"), False),               # unreachable: assume disallowed
])
def test_robots_failure_handling_follows_rfc_9309(robots, fetched):
    checker, net, _ = make_checker({"https://pmkisan.gov.in/robots.txt": robots,
                                    "https://pmkisan.gov.in/p": httpx.Response(200)}, retries=0)
    r = checker.check("https://pmkisan.gov.in/p")
    assert (r.outcome == "ok") is fetched
    assert ("https://pmkisan.gov.in/p" in net.urls()) is fetched


def test_robots_txt_redirect_is_followed_and_cached_per_origin():
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /nope\n")
    checker, net, _ = make_checker({
        "https://pmkisan.gov.in/robots.txt": httpx.Response(301, headers={"Location": "https://pmkisan.gov.in/static/robots.txt"}),
        "https://pmkisan.gov.in/static/robots.txt": robots,
        "https://pmkisan.gov.in/a": httpx.Response(200), "https://pmkisan.gov.in/b": httpx.Response(200)})
    assert checker.check("https://pmkisan.gov.in/nope").outcome == "skipped_robots"
    checker.check("https://pmkisan.gov.in/a")
    checker.check("https://pmkisan.gov.in/b")
    assert net.urls().count("https://pmkisan.gov.in/static/robots.txt") == 1  # fetched once


# ------------------------------------------------------------------ retries and politeness
def test_transient_errors_are_retried_with_exponential_backoff():
    calls = {"n": 0}

    def flaky(_req):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200)

    checker, _, sleeps = make_checker({"https://pmkisan.gov.in/f": flaky}, retries=2, backoff=1.0, min_host_interval=0)
    assert checker.check("https://pmkisan.gov.in/f").outcome == "ok"
    assert calls["n"] == 3 and sleeps == [1.0, 2.0]


def test_retry_after_header_is_honoured_and_capped():
    calls = {"n": 0}

    def limited(_req):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "999"}) if calls["n"] == 1 else httpx.Response(200)

    checker, _, sleeps = make_checker({"https://pmkisan.gov.in/l": limited}, min_host_interval=0)
    assert checker.check("https://pmkisan.gov.in/l").outcome == "ok"
    assert sleeps == [30.0]


def test_timeouts_and_network_errors_are_reported_not_raised():
    checker, _, _ = make_checker({"https://pmkisan.gov.in/t": httpx.ReadTimeout("slow"),
                                  "https://pmkisan.gov.in/n": httpx.ConnectError("dns")}, retries=1)
    assert checker.check("https://pmkisan.gov.in/t").outcome == "timeout"
    assert checker.check("https://pmkisan.gov.in/n").outcome == "network_error"


def test_requests_to_the_same_host_are_spaced_out():
    checker, _, sleeps = make_checker({"https://pmkisan.gov.in/a": httpx.Response(200),
                                       "https://pmkisan.gov.in/b": httpx.Response(200)}, min_host_interval=2.0)
    checker.check("https://pmkisan.gov.in/a")
    checker.check("https://pmkisan.gov.in/b")
    assert sleeps and all(abs(s - 2.0) < 1e-6 for s in sleeps)


def test_crawl_delay_from_robots_txt_is_honoured():
    robots = httpx.Response(200, text="User-agent: *\nCrawl-delay: 7\n")
    checker, _, sleeps = make_checker({"https://pmkisan.gov.in/robots.txt": robots,
                                       "https://pmkisan.gov.in/a": httpx.Response(200)}, min_host_interval=1.0)
    checker.check("https://pmkisan.gov.in/a")
    assert max(sleeps) == pytest.approx(7.0)


# ------------------------------------------------------------------ status mapping
@pytest.mark.parametrize("outcome,current,expected", [
    ("ok", "unverified", "auto_checked"),
    ("ok", "verified_manual", "verified_manual"),
    ("redirected_same_host", "verified_manual", "verified_manual"),
    ("redirected_other_host", "verified_manual", "redirected_unverified"),
    ("redirect_to_unsafe", "auto_checked", "suspicious"),
    ("broken", "verified_manual", "broken"),
    ("too_many_redirects", "unverified", "broken"),
    ("timeout", "verified_manual", "verified_manual"),
    ("skipped_robots", "unverified", "unverified"),
    ("skipped_policy", "unverified", "unverified"),
    ("inconclusive", "auto_checked", "auto_checked"),
    ("broken", "broken", "broken"),
    ("ok", "broken", "auto_checked"),
])
def test_status_mapping(outcome, current, expected):
    assert status_for_outcome(outcome, current) == expected


def test_checker_can_never_produce_verified_manual_from_a_lower_status():
    for outcome in ("ok", "redirected_same_host", "redirected_other_host", "broken", "inconclusive", "timeout"):
        for current in ("unverified", "auto_checked", "broken", "redirected_unverified", "suspicious"):
            assert status_for_outcome(outcome, current) != "verified_manual"


# ------------------------------------------------------------------ database recording
@pytest.fixture
def db(tmp_path):
    url = f"sqlite:///{tmp_path / 'links.db'}"
    init_database(url)
    eng = make_engine(url)
    with eng.begin() as c:
        c.execute(text("INSERT INTO schemes(scheme_id,level,created_at,updated_at) VALUES('s1','central','t','t')"))
        c.execute(text("INSERT INTO links(scheme_id,url,domain,link_type,domain_class,verification_status,verified_by,verified_at) "
                       "VALUES('s1','https://pmkisan.gov.in/a','pmkisan.gov.in','information','gov_in','verified_manual','spandan','2026-10-31')"))
        c.execute(text("INSERT INTO links(scheme_id,url,domain,link_type,domain_class) "
                       "VALUES('s1','https://pmkisan.gov.in/gone','pmkisan.gov.in','application','gov_in')"))
        c.execute(text("INSERT INTO links(scheme_id,url,domain,link_type,domain_class) "
                       "VALUES('s1','https://www.myscheme.gov.in/s/x','myscheme.gov.in','information','gov_in')"))
    yield eng
    eng.dispose()


def test_verify_links_records_history_and_updates_statuses(db):
    checker, _, _ = make_checker({"https://pmkisan.gov.in/a": httpx.Response(200),
                                  "https://pmkisan.gov.in/gone": httpx.Response(404)})
    with session_scope(db) as s:
        results = verify_links(s, checker)
    assert [r.outcome for _, r in results] == ["ok", "broken", "skipped_policy"]
    with db.connect() as c:
        rows = c.execute(text("SELECT url, verification_status, http_status, last_checked_at IS NOT NULL "
                              "FROM links ORDER BY link_id")).fetchall()
        assert [tuple(r) for r in rows] == [
            ("https://pmkisan.gov.in/a", "verified_manual", 200, 1),   # human verification preserved
            ("https://pmkisan.gov.in/gone", "broken", 404, 1),
            ("https://www.myscheme.gov.in/s/x", "unverified", None, 1)]
        assert c.execute(text("SELECT COUNT(*) FROM link_checks")).scalar() == 3
        assert c.execute(text("SELECT outcome FROM link_checks WHERE http_status=404")).scalar() == "broken"


def test_broken_result_overrides_manual_verification(db):
    checker, _, _ = make_checker({"https://pmkisan.gov.in/a": httpx.Response(404)})
    with session_scope(db) as s:
        verify_links(s, checker, scheme_id="s1")
    with db.connect() as c:
        assert c.execute(text("SELECT verification_status FROM links WHERE link_id=1")).scalar() == "broken"


def test_redirect_chain_is_stored_as_json(db):
    checker, _, _ = make_checker({
        "https://pmkisan.gov.in/a": httpx.Response(301, headers={"Location": "https://other.gov.in/z"}),
        "https://other.gov.in/z": httpx.Response(200)})
    with session_scope(db) as s:
        link = s.get(m.Link, 1)
        record_check(s, link, checker.check(link.url))
    with db.connect() as c:
        chain, status = c.execute(text("SELECT redirect_chain, verification_status FROM links WHERE link_id=1")).one()
    assert json.loads(chain) == ["https://other.gov.in/z"] and status == "redirected_unverified"


def test_verify_sources_script_never_contacts_prohibited_sites(monkeypatch, capsys):
    """--robots must not even fetch robots.txt from a site whose terms prohibit automated access."""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("verify_sources", "scripts/verify_sources.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    net = Net({"https://pmkisan.gov.in/robots.txt": httpx.Response(404)})
    monkeypatch.setattr(mod, "make_client", lambda: httpx.Client(transport=httpx.MockTransport(net)))
    mod.show_robots({"myscheme.gov.in": {"automated_access": "prohibited"},
                     "pmkisan.gov.in": {"automated_access": "unknown"}}, delay=0)
    out = capsys.readouterr().out
    assert "myscheme" in out and "no request sent" in out
    assert all("myscheme" not in u for u in net.urls())
    assert net.urls() == ["https://pmkisan.gov.in/robots.txt"]


def test_network_failures_report_the_real_reason():
    checker, _, _ = make_checker({"https://pmkisan.gov.in/robots.txt": httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] boom")},
                                 retries=0)
    r = checker.check("https://pmkisan.gov.in/p")
    assert r.outcome == "skipped_robots"
    assert "ConnectError" in r.detail and "CERTIFICATE_VERIFY_FAILED" in r.detail
