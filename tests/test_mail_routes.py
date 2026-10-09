from email.message import EmailMessage

from backend.domain_packs.mold.mail.parser import parse_message
from backend.domain_packs.mold.mail.rules import (
    DEFAULT_MAIL_ROUTE_MATCHERS,
    DEFAULT_MAIL_ROUTE_PLAN,
    MailRouteRule,
    classify_routes,
)


def test_initial_route_plan_has_eight_independent_routes():
    assert len(DEFAULT_MAIL_ROUTE_PLAN) == 8
    assert ("bu-04@rlj-metal.cn", "&XfJT0ZAB-", "QUOTATION") in DEFAULT_MAIL_ROUTE_PLAN
    assert ("dongxuequan@rlj-metal.cn", "INBOX", "CONSTRUCTION_START") in DEFAULT_MAIL_ROUTE_PLAN


def _parsed(subject="\u62a5\u4ef7\u901a\u77e5", body="\u8bf7\u67e5\u6536\u62a5\u4ef7"):
    message = EmailMessage()
    message["From"] = "sales@example.com"
    message["To"] = "bu-04@rlj-metal.cn"
    message["Subject"] = subject
    message.set_content(body)
    return parse_message(message.as_bytes())


def test_route_priority_selects_first_matching_category():
    route, status, reason = classify_routes(_parsed(), [
        MailRouteRule("quote", "quote", "INBOX", "INBOX", "QUOTATION", 10,
                      {"subject_keywords": ["\u62a5\u4ef7"]}),
        MailRouteRule("fallback", "fallback", "INBOX", "INBOX", "BID_AWARDED", 20, {}),
    ])
    assert route and route.category == "QUOTATION"
    assert status == "MATCHED"
    assert reason


def test_equal_priority_is_quarantined_for_review():
    route, status, reason = classify_routes(_parsed(), [
        MailRouteRule("a", "A", "INBOX", "INBOX", "QUOTATION", 10, {}),
        MailRouteRule("b", "B", "INBOX", "INBOX", "BID_AWARDED", 10, {}),
    ])
    assert route is None
    assert status == "REVIEW"
    assert reason


def test_route_sender_and_exclusion_rules_are_independent():
    route, status, _ = classify_routes(_parsed(body="\u62a5\u4ef7 \u8349\u7a3f"), [
        MailRouteRule("quote", "quote", "SENT", "SENT", "QUOTATION", 1,
                      {"exclude_keywords": ["\u8349\u7a3f"], "senders": ["sales@example.com"]}),
    ])
    assert route is None
    assert status == "UNMATCHED"


def test_subject_exclusion_only_checks_subject_and_normalizes_reply_prefixes():
    route, status, _ = classify_routes(_parsed(body="\u64a4\u9500\u90ae\u4ef6\u6210\u529f"), [
        MailRouteRule("quote", "quote", "INBOX", "INBOX", "QUOTATION", 1,
                      {"subject_keywords": ["\u62a5\u4ef7"],
                       "subject_exclude_keywords": ["\u64a4\u9500\u90ae\u4ef6\u6210\u529f"]}),
    ])
    assert route and status == "MATCHED"

    for subject in ("RE\uff1a\u62a5\u4ef7", "Re:\u62a5\u4ef7", "\u56de\u590d\uff1a\u62a5\u4ef7"):
        route, status, _ = classify_routes(_parsed(subject=subject), [
            MailRouteRule("quote", "quote", "INBOX", "INBOX", "QUOTATION", 1,
                          {"subject_keywords": ["\u62a5\u4ef7"],
                           "subject_exclude_keywords": ["\u56de\u590d:"]}),
        ])
        assert route is None and status == "UNMATCHED"


def test_default_route_matchers_cover_the_eight_initial_routes():
    assert len(DEFAULT_MAIL_ROUTE_MATCHERS) == 8
    assert DEFAULT_MAIL_ROUTE_MATCHERS[("bu-04@rlj-metal.cn", "&XfJT0ZAB-", "QUOTATION")]["subject_keywords"] == ["\u62a5\u4ef7\u5355"]

