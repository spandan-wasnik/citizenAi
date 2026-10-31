"""Polite link checker. A successful HTTP response never makes a link "verified".

What it does, in order, for each URL:
  1. assess the URL (suspicious links are never fetched);
  2. honour the source registry (automated_access == 'prohibited' is never fetched);
  3. honour robots.txt (RFC 9309: 4xx = no restrictions, 5xx/unreachable = do not fetch);
  4. HEAD (GET only if HEAD is refused with 405/501), following up to N redirects by hand,
     re-assessing every hop, never following to a suspicious/IP/local host (SSRF guard);
  5. throttle per host, honour Crawl-delay, retry transient failures with exponential backoff.

Outcome -> stored status (see ``status_for_outcome``): only 'auto_checked', 'broken',
'redirected_unverified' or 'suspicious' are ever set here. 'verified_manual' is set by a
person only; a broken or off-site redirect overrides it and needs human re-verification.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import models as m
from app.domain.domains import host_of
from app.domain.link_policy import assess_url

log = logging.getLogger(__name__)

USER_AGENT = "CitizenAssistAI/0.1 (link-check; student project)"
ROBOTS_TOKEN = "CitizenAssistAI"
TRANSIENT_STATUSES = {429, 502, 503, 504}
INCONCLUSIVE_STATUSES = {401, 403, 429, 999}  # sites often refuse automated clients; not proof of a broken link


@dataclass
class CheckResult:
    url: str
    outcome: str
    http_status: int | None = None
    final_url: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    detail: str | None = None
    checked_at: str = ""


def status_for_outcome(outcome: str, current: str) -> str:
    """Map a check outcome to links.verification_status. Never produces 'verified_manual'."""
    if outcome in ("ok", "redirected_same_host"):
        return current if current == "verified_manual" else "auto_checked"
    if outcome in ("redirected_other_host",):
        return "redirected_unverified"
    if outcome in ("redirect_to_unsafe",):
        return "suspicious"
    if outcome in ("broken", "too_many_redirects"):
        return "broken"
    return current  # inconclusive, timeout, network_error, skipped_*: no change


class _NetworkError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


class LinkChecker:
    def __init__(self, client: httpx.Client, *, registry: dict[str, dict] | None = None,
                 user_agent: str = USER_AGENT, max_redirects: int = 5, retries: int = 2,
                 backoff: float = 1.0, min_host_interval: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.registry = registry or {}
        self.user_agent = user_agent
        self.max_redirects, self.retries, self.backoff = max_redirects, retries, backoff
        self.min_host_interval = min_host_interval
        self._sleep, self._monotonic = sleep, monotonic
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, tuple[robotparser.RobotFileParser | None, str | None]] = {}
        self._crawl_delay: dict[str, float] = {}

    # ------------------------------------------------------------------ low level
    def _throttle(self, host: str) -> None:
        interval = max(self.min_host_interval, self._crawl_delay.get(host, 0.0))
        last = self._last_request.get(host)
        if last is not None:
            wait = interval - (self._monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = self._monotonic()

    def _send(self, method: str, url: str) -> httpx.Response:
        host = host_of(url)
        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                resp = self.client.request(method, url, headers={"User-Agent": self.user_agent},
                                           follow_redirects=False)
            except httpx.TimeoutException as exc:
                if attempt == self.retries:
                    raise _NetworkError("timeout", f"timed out after {attempt + 1} attempts") from exc
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise _NetworkError("network_error", type(exc).__name__) from exc
            else:
                if resp.status_code not in TRANSIENT_STATUSES or attempt == self.retries:
                    return resp
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    self._sleep(min(float(retry_after), 30.0))
                    continue
            self._sleep(self.backoff * (2 ** attempt))
        raise _NetworkError("network_error", "unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ robots.txt
    def robots_decision(self, url: str) -> tuple[bool, str | None]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._fetch_robots(origin, host_of(url))
        parser, note = self._robots[origin]
        if parser is None:
            return False, note
        return parser.can_fetch(ROBOTS_TOKEN, url), "disallowed by robots.txt"

    def _fetch_robots(self, origin: str, host: str) -> tuple[robotparser.RobotFileParser | None, str | None]:
        target = origin + "/robots.txt"
        try:
            for _ in range(self.max_redirects + 1):
                resp = self._send("GET", target)
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                    target = urljoin(target, resp.headers["Location"])
                    if assess_url(target, tuple(self.registry)).suspicious:
                        return None, "robots.txt redirected somewhere unsafe; not fetching"
                    continue
                break
            else:
                return None, "robots.txt redirected too many times; not fetching"
        except _NetworkError as exc:
            return None, f"robots.txt unreachable ({exc.kind}); not fetching"
        parser = robotparser.RobotFileParser()
        if 200 <= resp.status_code < 300:
            parser.parse(resp.text.splitlines())
            delay = parser.crawl_delay(ROBOTS_TOKEN)
            if delay:
                self._crawl_delay[host] = float(delay)
            return parser, None
        if 400 <= resp.status_code < 500:
            parser.parse([])  # RFC 9309: no robots.txt means no restrictions
            return parser, None
        return None, f"robots.txt returned {resp.status_code}; not fetching"

    # ------------------------------------------------------------------ the check
    def check(self, url: str) -> CheckResult:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = self._check(url)
        result.checked_at = now
        log.info("link check", extra={"url": url, "outcome": result.outcome, "http_status": result.http_status})
        return result

    def _check(self, url: str) -> CheckResult:
        assessment = assess_url(url, tuple(self.registry))
        if assessment.suspicious:
            return CheckResult(url, "skipped_unsafe", detail=", ".join(assessment.danger))
        entry = self.registry.get(assessment.host)
        if entry and entry.get("automated_access") == "prohibited":
            return CheckResult(url, "skipped_policy", detail=f"{assessment.host} prohibits automated access")
        allowed, why = self.robots_decision(url)
        if not allowed:
            return CheckResult(url, "skipped_robots", detail=why)

        chain: list[str] = []
        current, method = url, "HEAD"
        for _ in range(self.max_redirects + 1):
            try:
                resp = self._send(method, current)
            except _NetworkError as exc:
                return CheckResult(url, exc.kind, final_url=current, redirect_chain=chain, detail=exc.detail)
            code = resp.status_code
            if method == "HEAD" and code in (405, 501):
                method = "GET"
                continue
            if code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
                nxt = urljoin(current, resp.headers["Location"])
                hop = assess_url(nxt, tuple(self.registry))
                chain.append(nxt)
                if hop.suspicious:
                    return CheckResult(url, "redirect_to_unsafe", code, nxt, chain, ", ".join(hop.danger))
                hop_entry = self.registry.get(hop.host)
                if hop_entry and hop_entry.get("automated_access") == "prohibited":
                    return CheckResult(url, "redirected_other_host" if hop.host != assessment.host
                                       else "redirected_same_host", code, nxt, chain,
                                       "stopped: destination prohibits automated access")
                current = nxt
                continue
            return self._finish(url, assessment.host, current, code, chain)
        return CheckResult(url, "too_many_redirects", final_url=current, redirect_chain=chain)

    @staticmethod
    def _finish(url: str, origin_host: str, final_url: str, code: int, chain: list[str]) -> CheckResult:
        if 200 <= code < 300:
            if not chain:
                outcome = "ok"
            elif host_of(final_url) == origin_host:
                outcome = "redirected_same_host"
            else:
                outcome = "redirected_other_host"
        elif code in INCONCLUSIVE_STATUSES or code >= 500:
            outcome = "inconclusive"
        elif code >= 400:
            outcome = "broken"
        else:
            outcome = "inconclusive"
        return CheckResult(url, outcome, code, final_url, chain)


# ---------------------------------------------------------------------- database recording
def record_check(session: Session, link: m.Link, result: CheckResult) -> None:
    session.add(m.LinkCheck(link_id=link.link_id, checked_at=result.checked_at, http_status=result.http_status,
                            final_url=result.final_url, outcome=result.outcome))
    link.http_status = result.http_status
    link.final_url = result.final_url
    link.redirect_chain = json.dumps(result.redirect_chain) if result.redirect_chain else None
    link.last_checked_at = result.checked_at
    link.verification_status = status_for_outcome(result.outcome, link.verification_status)
    session.flush()


def verify_links(session: Session, checker: LinkChecker, scheme_id: str | None = None) -> list[tuple[m.Link, CheckResult]]:
    query = select(m.Link).order_by(m.Link.link_id)
    if scheme_id:
        query = query.where(m.Link.scheme_id == scheme_id)
    done = []
    for link in session.scalars(query):
        result = checker.check(link.url)
        record_check(session, link, result)
        done.append((link, result))
    return done
