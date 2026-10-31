"""Check official links politely (robots.txt, throttling, retries). Run this on YOUR machine.

    python scripts/verify_sources.py --robots              # read robots.txt of every registry domain
    python scripts/verify_sources.py --url https://pmkisan.gov.in/   # assess + check one URL
    python scripts/verify_sources.py                        # check every link in the database
    python scripts/verify_sources.py --scheme pm-kisan      # only one scheme's links

A result of 'ok' means the page answered; it does NOT mean the link is official or safe.
Only a person can mark a link verified_manual. Options: --delay SECONDS (per-host spacing, default 3).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib import robotparser

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.database.connection import make_engine, session_scope  # noqa: E402
from app.domain.link_policy import assess_url  # noqa: E402
from app.services.scheme_loader import load_registry  # noqa: E402
from scripts.init_db import init_database  # noqa: E402
from app.services.source_verifier import ROBOTS_TOKEN, USER_AGENT, LinkChecker, verify_links  # noqa: E402


def make_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(10.0), headers={"User-Agent": USER_AGENT})


def show_robots(registry: dict[str, dict], delay: float) -> int:
    checker = LinkChecker(make_client(), registry=registry, min_host_interval=delay)
    print(f"{'domain':32} {'root allowed':13} {'crawl-delay':12} note")
    for domain, entry in registry.items():
        if entry.get("automated_access") == "prohibited":
            print(f"{domain:32} {'(skipped)':13} {'-':12} registry says automated access is prohibited; no request sent")
            continue
        url = f"https://{domain}/"
        allowed, note = checker.robots_decision(url)
        parser, _ = checker._robots.get(f"https://{domain}", (None, None))
        cd = parser.crawl_delay(ROBOTS_TOKEN) if isinstance(parser, robotparser.RobotFileParser) else None
        print(f"{domain:32} {str(allowed):13} {str(cd or '-'):12} {'' if allowed else (note or '')}")
    print("\nIf a row says robots.txt was unreachable, the site may block automated clients; that is not an error here.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--robots", action="store_true")
    ap.add_argument("--scheme")
    ap.add_argument("--delay", type=float, default=3.0)
    args = ap.parse_args()

    registry = load_registry(ROOT / "data" / "sources" / "registry.yaml")
    if args.robots:
        return show_robots(registry, args.delay)

    checker = LinkChecker(make_client(), registry=registry, min_host_interval=args.delay)
    if args.url:
        a = assess_url(args.url, tuple(registry))
        print(f"host={a.host or '-'} class={a.domain_class} danger={list(a.danger)} warnings={list(a.warnings)}")
        r = checker.check(args.url)
        print(f"outcome={r.outcome} http={r.http_status} final={r.final_url} chain={r.redirect_chain} detail={r.detail}")
        return 0

    url = get_settings().database_url
    init_database(url)  # safe and idempotent: makes sure the tables exist
    engine = make_engine(url)
    try:
        with session_scope(engine) as session:
            results = verify_links(session, checker, scheme_id=args.scheme)
    finally:
        engine.dispose()
    if not results:
        print("No links in the database yet (add scheme files, then run scripts/seed_database.py).")
    for link, r in results:
        print(f"[{r.outcome:22}] {link.scheme_id:24} {link.url}  -> status now: {link.verification_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
