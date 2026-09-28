from email.message import EmailMessage

import pytest

from backend.domain_packs.mold.mail.parser import (
    MailDocumentLimitError,
    classify_document,
    extract_structured_documents,
    message_documents,
    parse_message,
    sender_is_allowed,
)


def _message(*, subject="周齐套", sender="supplier@example.com", body="", html=None):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["Date"] = "Mon, 28 Sep 2026 10:00:00 +0800"
    message["Message-ID"] = "<mail-1@example.com>"
    if html is None:
        message.set_content(body)
    else:
        message.set_content(body)
        message.add_alternative(html, subtype="html")
    return message


def test_attachment_is_authoritative_and_classified_from_subject():
    message = _message(body="正文也包含 | 新品 | 字样")
    message.add_attachment(b"a,b\n1,2\n", maintype="text", subtype="csv", filename="供应商表.csv")

    parsed = parse_message(message.as_bytes(), allowed_senders=("example.com",))

    assert parsed.sender_allowed is True
    assert parsed.business_types == ("WEEKLY_KIT",)
    assert len(parsed.documents) == 1
    assert parsed.documents[0].source == "attachment"
    assert parsed.documents[0].data == b"a,b\n1,2\n"


def test_markdown_body_table_is_extracted_when_no_attachment():
    message = _message(
        subject="新品计划",
        body="请处理：\n| 物料 | 数量 |\n| --- | --- |\n| A | 3 |\n",
    )

    parsed = parse_message(message.as_bytes(), allowed_senders=("supplier@example.com",))

    assert len(parsed.documents) == 1
    assert parsed.documents[0].source == "body_table"
    assert parsed.documents[0].data == "\ufeff物料,数量\nA,3\n".encode()
    assert parsed.business_types == ("NEW_PRODUCT",)


def test_html_table_is_extracted():
    message = _message(
        subject="出口日输",
        body="fallback",
        html="<table><tr><th>物料</th><th>数量</th></tr><tr><td>B</td><td>4</td></tr></table>",
    )
    _, documents, _, _ = message_documents(message.as_bytes(), "mail")
    assert documents[0].source == "body_table"
    assert "物料,数量".encode() in documents[0].data


def test_sender_allowlist_fails_closed_for_multiple_from_addresses():
    assert sender_is_allowed("Supplier <supplier@example.com>", ("example.com",))
    assert not sender_is_allowed("a@example.com, b@example.com", ("example.com",))
    assert not sender_is_allowed("spoof@example.net", ("example.com",))


def test_classification_prefers_subject_and_rejects_ambiguity():
    assert classify_document("新品计划", "齐套.csv", "", None)[0] == "NEW_PRODUCT"
    business, reason = classify_document("新品和齐套", "data.csv", "", None)
    assert business is None
    assert "多个业务类型" in reason


def test_message_size_limit_is_enforced(monkeypatch):
    import backend.domain_packs.mold.mail.parser as parser

    monkeypatch.setattr(parser, "MAX_MESSAGE_BYTES", 4)
    with pytest.raises(MailDocumentLimitError):
        message_documents(b"12345", "mail")


def test_extract_structured_documents_uses_existing_parsed_message():
    message = _message(body="| 物料 | 数量 |\n| -- | -- |\n| 1 | 2 |")
    assert extract_structured_documents(message)[0].source == "body_table"
