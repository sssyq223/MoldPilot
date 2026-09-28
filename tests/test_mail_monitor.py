from pathlib import Path

from email.message import EmailMessage

from backend.domain_packs.mold.mail.monitor import MailCursor, MailMonitor, MailMonitorConfig


class FakeImap:
    def __init__(self, messages):
        self.messages = messages
        self.logged_out = False

    def select(self, folder, readonly=False):
        assert folder == "INBOX"
        assert readonly is True
        return "OK", [b"1"]

    def status(self, folder, query):
        return "OK", [b"INBOX (UIDVALIDITY 9 UIDNEXT 3)"]

    def uid(self, operation, *args):
        if operation == "search":
            return "OK", [b"1 2"]
        if operation == "fetch":
            return "OK", [(f"{args[0]} RFC822".encode(), self.messages[int(args[0])])]
        raise AssertionError(operation)

    def logout(self):
        self.logged_out = True


class Ledger:
    def __init__(self):
        self.cursor = MailCursor()
        self.records = []
        self.failures = []

    def load_cursor(self, account_id):
        return self.cursor

    def save_cursor(self, account_id, cursor):
        self.cursor = cursor

    def record_message(self, account, uid, uid_validity, raw, parsed, raw_sha256, archive_path):
        self.records.append((uid, parsed, Path(archive_path)))

    def record_failure(self, account, uid, uid_validity, raw_sha256, error):
        self.failures.append((uid, error))


def _mail(subject):
    message = EmailMessage()
    message["From"] = "supplier@example.com"
    message["Subject"] = subject
    message.set_content("| 物料 | 数量 |\n| --- | --- |\n| A | 1 |")
    return message.as_bytes()


def test_poll_is_readonly_bounded_and_archives_content(tmp_path):
    ledger = Ledger()
    fake = FakeImap({1: _mail("周齐套"), 2: _mail("新品计划")})
    config = MailMonitorConfig(
        account_id="a1", account_name="supplier", host="imap.test", port=993,
        username="robot", password="secret", allowed_senders=("example.com",),
    )
    monitor = MailMonitor(config, ledger, client_factory=lambda _: fake, archive_root=tmp_path)

    stats = monitor.poll_once()

    assert stats.scanned == 2
    assert stats.accepted == 2
    assert ledger.cursor == MailCursor("9", 2)
    assert len(ledger.records) == 2
    assert all(path.exists() and path.suffix == ".eml" for _, _, path in ledger.records)
    assert fake.logged_out is True


def test_uidvalidity_reset_replays_from_uid_one(tmp_path):
    ledger = Ledger()
    ledger.cursor = MailCursor("old", 99)
    fake = FakeImap({1: _mail("出口日输"), 2: _mail("13周预测")})
    config = MailMonitorConfig("a1", "supplier", "imap.test", 993, "robot", "secret")
    stats = MailMonitor(config, ledger, client_factory=lambda _: fake, archive_root=tmp_path).poll_once()
    assert stats.scanned == 2
    assert ledger.cursor == MailCursor("9", 2)
