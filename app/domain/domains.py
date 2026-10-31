"""Pure URL/domain helpers. The fuller link classifier (suspicious domains, redirects) is step 1.6."""
from __future__ import annotations

from urllib.parse import urlsplit


def host_of(url: str) -> str:
    """Lower-cased hostname without port, userinfo or a leading 'www.'. Empty string if none."""
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def classify_domain(host: str) -> str:
    """'gov_in', 'nic_in' or 'unrecognized'. Matches on label boundaries, so 'evilgov.in' fails."""
    if host == "gov.in" or host.endswith(".gov.in"):
        return "gov_in"
    if host == "nic.in" or host.endswith(".nic.in"):
        return "nic_in"
    return "unrecognized"
