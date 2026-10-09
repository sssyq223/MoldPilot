"""Deterministic, provider-neutral mail route matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

from .parser import ParsedMail, sender_is_allowed

MAIL_CATEGORIES = (
    "QUOTATION",
    "BID_AWARDED",
    "CONSTRUCTION_START",
    "PROJECT_KICKOFF",
)

DEFAULT_MAIL_ROUTE_PLAN = (
    ("bu-04@rlj-metal.cn", "INBOX", "QUOTATION"),
    ("bu-04@rlj-metal.cn", "INBOX", "BID_AWARDED"),
    ("bu-04@rlj-metal.cn", "INBOX", "PROJECT_KICKOFF"),
    ("bu-04@rlj-metal.cn", "&XfJT0ZAB-", "QUOTATION"),
    ("bu-05@rlj-metal.cn", "INBOX", "BID_AWARDED"),
    ("bu-05@rlj-metal.cn", "INBOX", "PROJECT_KICKOFF"),
    ("dongxuequan@rlj-metal.cn", "INBOX", "CONSTRUCTION_START"),
    ("bu-10@rlj-metal.cn", "INBOX", "BID_AWARDED"),
)

DEFAULT_MAIL_ROUTE_MATCHERS = {
    ("bu-04@rlj-metal.cn", "INBOX", "QUOTATION"):
        {"subject_keywords": ["\u62a5\u4ef7"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]},
    ("bu-04@rlj-metal.cn", "&XfJT0ZAB-", "QUOTATION"):
        {"subject_keywords": ["\u62a5\u4ef7\u5355"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]},
    ("bu-05@rlj-metal.cn", "INBOX", "BID_AWARDED"):
        {"subject_keywords": ["\u4e2d\u6807\u901a\u77e5"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f", "\u56de\u590d:"]},
    ("bu-04@rlj-metal.cn", "INBOX", "BID_AWARDED"):
        {"subject_keywords": ["\u4e2d\u6807"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]},
    ("bu-10@rlj-metal.cn", "INBOX", "BID_AWARDED"):
        {"subject_keywords": ["\u4e2d\u6807"],
         "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f", "\u56de\u590d:", "\u8f6c\u53d1:", "Re:"]},
    ("dongxuequan@rlj-metal.cn", "INBOX", "CONSTRUCTION_START"):
        {"subject_keywords": ["\u4e0b\u53d1\u5f00\u5de5\u901a\u77e5"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]},
    ("bu-04@rlj-metal.cn", "INBOX", "PROJECT_KICKOFF"):
        {"subject_keywords": ["\u542f\u52a8"],
         "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f", "\u6d41\u7a0b", "\u542f\u52a8\u4f1a"]},
    ("bu-05@rlj-metal.cn", "INBOX", "PROJECT_KICKOFF"):
        {"subject_keywords": ["\u542f\u52a8\u901a\u77e5"], "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]},
}


@dataclass(frozen=True)
class MailRouteRule:
    route_id: str
    name: str
    folder: str
    direction: str
    category: str
    priority: int = 100
    matcher: dict[str, Any] | None = None
    allowed_senders: tuple[str, ...] = ()
    notify_inbox: bool = True
    archive: bool = True


def normalize_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = normalize_text(value)
        return (normalized,) if normalized else ()
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(dict.fromkeys(
        normalized for item in value
        if (normalized := normalize_text(item))
    ))


def _subject_exclusion_values(values: Iterable[str]) -> tuple[str, ...]:
    result = set(_values(values))
    if result.intersection({"re:", "\u56de\u590d:"}):
        result.update({"re:", "\u56de\u590d:"})
    if result.intersection({"fw:", "fwd:", "\u8f6c\u53d1:"}):
        result.update({"fw:", "fwd:", "\u8f6c\u53d1:"})
    return tuple(sorted(result))


def _haystack(parsed: ParsedMail) -> str:
    names = " ".join(item.name for item in parsed.attachments or parsed.documents)
    return normalize_text("\n".join((parsed.subject, parsed.plain_body, parsed.html_body, names)))


def _contains_all(haystack: str, values: Iterable[str]) -> bool:
    return all(value in haystack for value in values)


def _contains_any(haystack: str, values: Iterable[str]) -> bool:
    values = tuple(values)
    return not values or any(value in haystack for value in values)


def match_route(parsed: ParsedMail, route: MailRouteRule) -> tuple[bool, str]:
    matcher = route.matcher or {}
    if route.category not in MAIL_CATEGORIES:
        return False, "invalid_category"
    if route.allowed_senders and not sender_is_allowed(parsed.sender, route.allowed_senders):
        return False, "sender_not_allowed"

    subject = normalize_text(parsed.subject)
    body = normalize_text(parsed.plain_body or parsed.html_body)
    haystack = _haystack(parsed)
    reasons: list[str] = []

    senders = _values(matcher.get("senders", matcher.get("allowed_senders")))
    if senders and not sender_is_allowed(parsed.sender, senders):
        return False, "sender_mismatch"
    recipients = _values(matcher.get("recipients"))
    if recipients and not any(value in set(parsed.recipients) | set(parsed.cc) for value in recipients):
        return False, "recipient_mismatch"

    subject_keywords = _values(matcher.get("subject_keywords"))
    subject_exclude_keywords = _subject_exclusion_values(matcher.get("subject_exclude_keywords", ()))
    body_keywords = _values(matcher.get("body_keywords"))
    all_keywords = _values(matcher.get("all_keywords"))
    any_keywords = _values(matcher.get("any_keywords"))
    exclude_keywords = _values(matcher.get("exclude_keywords"))

    if subject_keywords and not _contains_all(subject, subject_keywords):
        return False, "subject_keywords_missed"
    if subject_exclude_keywords and any(value in subject for value in subject_exclude_keywords):
        return False, "subject_excluded"
    if body_keywords and not _contains_all(body, body_keywords):
        return False, "body_keywords_missed"
    if all_keywords and not _contains_all(haystack, all_keywords):
        return False, "all_keywords_missed"
    if any_keywords and not _contains_any(haystack, any_keywords):
        return False, "any_keywords_missed"
    if exclude_keywords and any(value in haystack for value in exclude_keywords):
        return False, "excluded"
    if subject_keywords:
        reasons.append("subject_keywords")
    if subject_exclude_keywords:
        reasons.append("subject_exclude_keywords")
    if body_keywords:
        reasons.append("body_keywords")
    if all_keywords or any_keywords:
        reasons.append("content_keywords")

    attachment_names = tuple(
        normalize_text(item.name) for item in parsed.attachments or parsed.documents
    )
    suffixes = _values(matcher.get("attachment_extensions", matcher.get("extensions")))
    if suffixes:
        normalized = tuple(value if value.startswith(".") else f".{value}" for value in suffixes)
        if not any(name.endswith(suffix) for name in attachment_names for suffix in normalized):
            return False, "attachment_extension_missed"
        reasons.append("attachment_extensions")
    attachment_keywords = _values(matcher.get("attachment_names"))
    if attachment_keywords and not any(
        keyword in name for name in attachment_names for keyword in attachment_keywords
    ):
        return False, "attachment_name_missed"
    if attachment_keywords:
        reasons.append("attachment_names")

    regex = matcher.get("regex")
    if regex:
        try:
            if not re.search(str(regex), haystack, flags=re.IGNORECASE):
                return False, "regex_missed"
        except re.error:
            return False, "invalid_regex"
        reasons.append("regex")

    return True, "; ".join(reasons) or "default"


def classify_routes(
    parsed: ParsedMail, routes: Iterable[MailRouteRule]
) -> tuple[MailRouteRule | None, str, str]:
    candidates = sorted(routes, key=lambda item: (item.priority, item.name, item.route_id))
    matches: list[tuple[MailRouteRule, str]] = []
    for route in candidates:
        matched, reason = match_route(parsed, route)
        if matched:
            matches.append((route, reason))
    if not matches:
        return None, "UNMATCHED", "no_route"
    best_priority = matches[0][0].priority
    best = [(route, reason) for route, reason in matches if route.priority == best_priority]
    if len(best) > 1:
        names = ", ".join(route.name for route, _ in best)
        return None, "REVIEW", f"same_priority:{names}"
    route, reason = best[0]
    return route, "MATCHED", reason

