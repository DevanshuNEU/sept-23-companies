"""Microsoft bounce notices read from Mail.app (multi-sender plan, Task B3).

The four fixtures in `tests/fixtures/ndr` are real notices his Exchange mailbox received, with every third-party
address replaced. Each goes through `FakeMailApp` (the real client with its runner replaced), so the text the parser
sees is what Mail.app's message script returns: the first 2000 characters of the body.
"""
import email
import unittest
from pathlib import Path

import dataclasses

from outreach.sending import accounts, inbound
from outreach.sending.classify import classify
from outreach.sending.fakemailapp import FakeMailApp
from outreach.sending.mail import Outgoing
from outreach.store import transaction
from tests.sendtest import iso, seed_sent
from tests.test_sending_inbound import MULTI as SHIPPED, Base as InboundBase

FIXTURES = Path(__file__).parent / "fixtures" / "ndr"
# The Northeastern account sending, as it does once he turns it on: a block pauses only an account that sends.
MULTI = dataclasses.replace(SHIPPED, accounts=tuple(
    dataclasses.replace(a, sends=True) if a.kind == "mailapp" else a for a in SHIPPED.accounts))
NEU = next(a for a in MULTI.accounts if a.kind == "mailapp")
# fixture -> (the failed address, the subject its notice carries is read from the file)
CASES = {
    "exchange_own_tenant_5_1_1.txt": "jd@acme-astro.example",
    "o365_recipient_tenant_5_1_10.txt": "founders@acme-fund.example",
    "proofpoint_5_4_1.txt": "mc@acme-tree.example",
    "onprem_exchange_5_1_10.txt": "rn@acme-ins.example",
}


def fixture(name: str):
    return email.message_from_string((FIXTURES / name).read_text())


class MicrosoftBase(InboundBase):
    def setUp(self):
        super().setUp()
        self.rows = accounts.sync(self.db, MULTI, iso(self.clock()))
        self.row = self.rows[NEU.address]
        self.mail = FakeMailApp(clock=self.clock, account=NEU, reconcile_after_minutes=MULTI.reconcile_after_minutes)
        self.read()  # the first read only sets the cursor; the notices below arrive after it

    def sent_from_neu(self, address="ada@acme.example"):
        """A first email the Northeastern account sent through Mail.app. Returns (touch id, Sent)."""
        person = self.person(email=address)
        self.n += 1
        token = f"<claim{self.n}@knock.local>"
        sent = self.mail.send(Outgoing(to=address, subject=f"A question about your platform {self.n}",
                                       body="Hi.", from_name=MULTI.from_name, from_addr=NEU.address,
                                       message_id=token, thread_id=None, in_reply_to=None, references=(),
                                       marker=token))
        seed_sent(self.db, person, at=self.clock(), rfc=sent.rfc_message_id, thread=sent.thread_id,
                  account_id=self.row.account_id)
        touch = self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0]
        return touch, sent

    def deliver(self, name, *, in_reply_to=None, mailbox="Inbox"):
        """The fixture notice lands in the mailbox: its own headers (In-Reply-To only when `in_reply_to` is given),
        its readable text as the body."""
        msg = fixture(name)
        keep = {k: v for k, v in msg.items() if k.lower() in ("content-type", "auto-submitted")}
        if in_reply_to:
            keep["In-Reply-To"] = in_reply_to
            keep["References"] = in_reply_to
        return self.mail.add_to_mailbox(mailbox, msg["From"], NEU.address, msg["Subject"], headers=keep,
                                        content=msg.get_payload())

    def read(self):
        read = inbound.read_inbound(self.db, self.mail, self.row, cfg=MULTI, now=self.clock())
        with transaction(self.db):
            counts = inbound.apply_inbound(self.db, read, self.row, now=self.clock(), cfg=MULTI, notify=self.notify)
        return read, counts

    def meta_of(self, stored):
        return self.mail.message("mailapp:" + stored.rfc_id)

    def neu_row(self):
        return self.db.execute("SELECT * FROM sender_account WHERE account_id = ?",
                               (self.row.account_id,)).fetchone()


class FixtureTest(MicrosoftBase):
    def test_exchange_ndr_is_bounce_with_address(self):
        stored = self.deliver("exchange_own_tenant_5_1_1.txt")
        found = classify(self.meta_of(stored), NEU.address)
        self.assertEqual((found.kind, found.dsn_class, found.failed_recipient),
                         ("bounce", "hard", "jd@acme-astro.example"))

    def test_every_fixture_is_a_hard_bounce_with_its_address(self):
        for name, address in CASES.items():
            with self.subTest(name):
                found = classify(self.meta_of(self.deliver(name)), NEU.address)
                self.assertEqual((found.kind, found.dsn_class, found.failed_recipient), ("bounce", "hard", address))

    def test_ndr_matched_by_in_reply_to(self):
        touch, sent = self.sent_from_neu("someone.else@acme.example")  # the notice names another address
        self.deliver("exchange_own_tenant_5_1_1.txt", in_reply_to=sent.rfc_message_id)
        _, counts = self.read()
        self.assertEqual(counts["bounces"], 1)
        self.assertEqual(self.touch(touch)["state"], "bounced")

    def test_each_fixture_bounces_the_touch_of_its_address(self):
        for name, address in CASES.items():
            with self.subTest(name):
                touch, _ = self.sent_from_neu(address)
                self.deliver(name)  # no In-Reply-To the store knows: matched by the failed address
                _, counts = self.read()
                self.assertEqual(counts["bounces"], 1)
                self.assertEqual(self.touch(touch)["state"], "bounced")
                self.assertEqual(self.db.execute("SELECT count(*) FROM bounced_address WHERE address = ?",
                                                 (address,)).fetchone()[0], 1)
                self.assertEqual(self.neu_row()["paused"], 0)
                self.clock.advance(minutes=1)

    def test_notice_for_an_address_we_never_emailed_is_not_matched(self):
        touch, _ = self.sent_from_neu("ada@acme.example")
        self.deliver("proofpoint_5_4_1.txt")
        _, counts = self.read()
        self.assertEqual(counts["bounces"], 0)
        self.assertEqual(self.touch(touch)["state"], "open")


class StatusTest(MicrosoftBase):
    def notice(self, text, *, subject="Undeliverable: A question about your platform 1"):
        return self.mail.add_to_mailbox(
            "Inbox", "Microsoft Outlook <MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@northeastern.onmicrosoft.com>",
            NEU.address, subject, headers={"Auto-Submitted": "auto-replied",
                                          "Content-Type": "multipart/report; report-type=delivery-status"},
            content=text)

    def test_4xx_is_delay(self):
        touch, sent = self.sent_from_neu()
        stored = self.notice("Your message to ada@acme.example couldn't be delivered yet. Status code: 450 4.4.7 "
                             "Message expired; the server will keep trying.")
        found = classify(self.meta_of(stored), NEU.address)
        self.assertEqual((found.kind, found.failed_recipient), ("delay", "ada@acme.example"))
        _, counts = self.read()
        self.assertEqual(counts["bounces"], 0)
        self.assertEqual(self.touch(touch)["state"], "open")

    def test_restricted_sender_pauses_account(self):
        for code, words in (("5.7.708", "Access denied, traffic not accepted from this IP."),
                            ("5.7.705", "Tenant has exceeded threshold, sending is blocked."),
                            ("5.1.8", "Access denied, bad outbound sender."),
                            ("5.7.1", "Your account is restricted from sending email.")):
            with self.subTest(code):
                self.setUp()
                touch, sent = self.sent_from_neu()
                self.notice(f"Your message to ada@acme.example couldn't be delivered. Status code: 550 {code} {words}")
                _, counts = self.read()
                self.assertEqual(counts["bounces"], 0)
                self.assertEqual(self.touch(touch)["state"], "open")  # the address is fine: the account is blocked
                row = self.neu_row()
                self.assertEqual((row["paused"], row["pause_kind"]), (1, "hard_block"))
                self.assertEqual(len(self.events("sender", "provider_block", self.row.account_id)), 1)
                self.assertEqual(self.sender()["paused"], 0)
                self.assertEqual(self.account_row()["paused"], 0)

    def test_recipient_side_5_1_1_is_not_a_block(self):
        found = classify(self.meta_of(self.notice("Your message to a@b.example couldn't be delivered. 550 5.1.1")),
                         NEU.address)
        self.assertEqual(found.dsn_class, "hard")


if __name__ == "__main__":
    unittest.main()
