"""Pure display helpers for the Streamlit app. No Streamlit and no backend imports, so they are easy to test."""
from __future__ import annotations

from typing import Any

STATUS_ORDER = ["LIKELY_ELIGIBLE", "INSUFFICIENT_INFORMATION", "REQUIRES_OFFICIAL_VERIFICATION", "LIKELY_INELIGIBLE"]
STATUS_ICON = {"LIKELY_ELIGIBLE": "🟢", "INSUFFICIENT_INFORMATION": "🟡",
               "REQUIRES_OFFICIAL_VERIFICATION": "🟠", "LIKELY_INELIGIBLE": "🔴"}
STATUS_HELP = {
    "LIKELY_ELIGIBLE": "Every rule we could check is met, and our record is verified and complete.",
    "INSUFFICIENT_INFORMATION": "We need more answers from you before we can say anything useful.",
    "REQUIRES_OFFICIAL_VERIFICATION": "Our record is not complete or verified enough to give a firm answer. Check the official source.",
    "LIKELY_INELIGIBLE": "A verified rule is clearly not met.",
}
VERIFICATION_TEXT = {
    "draft": "Draft: not checked against an official source",
    "source_verified": "Checked against the official text we were given; not yet reviewed by a person",
    "human_reviewed": "Reviewed by a person",
}
SCHEME_STATUS_TEXT = {"active": "Recorded as active", "closed": "Recorded as closed",
                      "suspended": "Recorded as suspended", "unknown": "Current status not confirmed"}
LEVEL_TEXT = {"central": "Central", "state": "State", "ut": "Union territory", "local": "Local"}
RESULT_ICON = {"PASS": "✅", "FAIL": "❌", "UNKNOWN": "❓", "OFFICIAL_ONLY": "🔎"}


def build_profile(answers: dict[str, Any]) -> dict[str, Any]:
    """Keep only the questions the person actually answered. Blank means 'not provided', never zero or no."""
    return {k: v for k, v in answers.items() if v is not None and v != "" and not (isinstance(v, str) and not v.strip())}


def rule_icon(rule: dict[str, Any]) -> str:
    """For a requirement PASS is good; for an exclusion PASS means the exclusion applies, which is bad."""
    result = rule["result"]
    if rule.get("purpose") == "exclusion" and result in ("PASS", "FAIL"):
        return "❌" if result == "PASS" else "✅"
    return RESULT_ICON.get(result, "❓")


def group_results(results: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Results grouped in a fixed, explainable order; empty groups are left out."""
    out = []
    for status in STATUS_ORDER:
        items = [r for r in results if r["status"] == status]
        if items:
            out.append((status, items))
    return out


def question_priorities(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which unanswered questions would help the most schemes. Ties are broken by label so the order is stable."""
    agg: dict[str, dict[str, Any]] = {}
    for r in results:
        for m in r.get("missing", []):
            e = agg.setdefault(m["field"], {"field": m["field"], "label": m["label"], "sensitive": m["sensitive"],
                                            "schemes": []})
            if r.get("name", r["scheme_id"]) not in e["schemes"]:
                e["schemes"].append(r.get("name", r["scheme_id"]))
    return sorted(agg.values(), key=lambda e: (-len(e["schemes"]), e["label"]))


def format_value(info: dict[str, Any], value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    labels = info.get("value_labels") or {}
    if str(value) in labels:
        return labels[str(value)]
    if info.get("type") == "money_inr" and isinstance(value, int):
        from_right = str(abs(value))
        if len(from_right) > 3:
            head, tail, groups = from_right[:-3], from_right[-3:], []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            from_right = ",".join(groups + [tail])
        return "₹" + from_right
    return str(value).replace("_", " ")


def tree_lines(node: dict[str, Any] | None, *, exclusion: bool = False, depth: int = 0) -> list[str]:
    """The rule tree as markdown bullet lines. Each rule keeps its locator so the person can check it."""
    if not node:
        return []
    pad = "  " * depth
    if node["type"] == "rule":
        bits = [f"{pad}- **{node['label']}**: {node['requirement']}"]
        if not node.get("checkable_from_profile"):
            bits.append("*(cannot be checked from your answers)*")
        if node.get("locator"):
            bits.append(f"· source: {node['locator']}")
        return [" ".join(bits)]
    lead = {"AND": "All of these must be met", "OR": "At least one of these must be met"}.get(node["logic"], node["logic"])
    lines = [] if exclusion else [f"{pad}- *{lead}:*"]
    for item in node["items"]:
        lines += tree_lines(item, exclusion=exclusion, depth=depth + (0 if exclusion else 1))
    return lines


def collect_sources(detail: dict[str, Any]) -> list[dict[str, str]]:
    """Every distinct official source the detail page rests on, in order of first use."""
    seen: dict[str, dict[str, str]] = {}

    def add(url: str | None, title: str | None = None) -> None:
        if not url:
            return
        if url not in seen:
            seen[url] = {"url": url, "title": title or url}
        elif title and seen[url]["title"] == url:
            seen[url]["title"] = title

    def walk(node: dict[str, Any] | None) -> None:
        if not node:
            return
        if node["type"] == "rule":
            add(node.get("source_url"))
        else:
            for i in node["items"]:
                walk(i)

    walk(detail.get("eligibility"))
    for e in detail.get("exclusions", []):
        walk(e)
    for key in ("other_conditions", "documents", "steps"):
        for row in detail.get(key, []):
            src = row.get("source") or {}
            add(src.get("url"), src.get("title"))
    return list(seen.values())
