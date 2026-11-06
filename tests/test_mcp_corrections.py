"""update_person, mark_email_bounced and move_person (#181), called through the official SDK's client on a scratch store.

Claude can fix a person after the fact. Every change is one `event` with the old value and one flagged review item for
Devanshu; nothing here writes a touch or a message, so a cleared address is stopped by the sender's own `no_address`
hold."""
import json
import unittest
from datetime import datetime, timezone

from outreach.sending import queue
from outreach.sending.config import load_config as load_send_config
from tests import apiseed as seed
from tests.mcptest import NOW, McpCase, fingerprint
from tests.test_mcp_reads import merge, no_go


class CorrectionsCase(McpCase):
    def setUp(self):
        super().setUp()
        self.acme = seed.company(self.db, "Acme", "working")
        self.pat = seed.person(self.db, self.acme, "Pat Lee", email="pat@acme.example")

    def row(self, person_id: int):
        return self.db.execute("SELECT * FROM person WHERE person_id = ?", (person_id,)).fetchone()

    def events(self, kind: str):
        return [dict(r) | {"detail": json.loads(r["detail"])} for r in self.db.execute(
            "SELECT * FROM event WHERE entity = 'person' AND kind = ? ORDER BY event_id", (kind,))]

    def items(self):
        return self.db.execute("SELECT * FROM review_item ORDER BY review_id").fetchall()


class UpdatePersonTest(CorrectionsCase):
    def test_update_changes_only_sent_fields_and_logs_old_values(self):
        got = self.ok("update_person", {"person_id": self.pat, "name": "Patricia Lee", "title": "Staff Engineer"})
        self.assertEqual(sorted(got["changed"]), ["name", "title"])
        row = self.row(self.pat)
        self.assertEqual((row["name"], row["title"], row["email"], row["revision"]),
                         ("Patricia Lee", "Staff Engineer", "pat@acme.example", 2))
        (event,) = self.events("edited")
        self.assertEqual(event["actor"], "claude")
        self.assertEqual(event["entity_id"], self.pat)
        self.assertEqual(event["detail"]["name"], {"old": "Pat Lee", "new": "Patricia Lee"})
        self.assertEqual(event["detail"]["title"], {"old": "Software Engineer", "new": "Staff Engineer"})
        (item,) = self.items()
        self.assertEqual((item["kind"], item["raised_by"], item["state"], item["person_id"], item["company_id"]),
                         ("flagged", "claude", "open", self.pat, self.acme))
        self.assertIn("Pat Lee", item["reason"])
        self.assertEqual(item["review_id"], got["review_id"])

    def test_email_null_clears_it_and_moves_an_email_person_to_linkedin(self):
        got = self.ok("update_person", {"person_id": self.pat, "email": None})
        row = self.row(self.pat)
        self.assertEqual((row["email"], row["channel"], row["linkedin_reason"]), (None, "linkedin", "no_email"))
        self.assertEqual(got["channel"], "linkedin")
        self.assertEqual(self.events("edited")[0]["detail"]["email"], {"old": "pat@acme.example", "new": None})

    def test_email_on_a_linkedin_person_flips_to_email(self):
        sam = seed.person(self.db, self.acme, "Sam Park", channel="linkedin")
        self.ok("update_person", {"person_id": sam, "email": "sam@acme.example"})
        row = self.row(sam)
        self.assertEqual((row["email"], row["channel"], row["linkedin_reason"]), ("sam@acme.example", "email", None))

    def test_a_replacement_email_is_lowercased_and_checked(self):
        self.ok("update_person", {"person_id": self.pat, "email": " Pat.Lee@Acme.Example "})
        self.assertEqual(self.row(self.pat)["email"], "pat.lee@acme.example")
        before = fingerprint(self.db)
        self.assertEqual(self.error("update_person", {"person_id": self.pat, "email": "nope"})["code"], "bad_input")
        self.assertEqual(fingerprint(self.db), before)

    def test_an_address_already_on_someone_else_is_refused_and_writes_nothing(self):
        seed.person(self.db, self.acme, "Sam Park", email="sam@acme.example")
        before = fingerprint(self.db)
        err = self.error("update_person", {"person_id": self.pat, "email": "sam@acme.example"})
        self.assertEqual(err["code"], "email_in_use")
        self.assertIn("Sam Park", err["fix"])
        self.assertEqual(fingerprint(self.db), before)

    def test_replacing_after_a_sent_first_email_is_refused_unless_it_bounced(self):
        sent = seed.touch(self.db, self.pat)
        seed.send_first(self.db, sent)
        before = fingerprint(self.db)
        self.assertEqual(self.error("update_person", {"person_id": self.pat, "email": "new@acme.example"})["code"],
                         "email_sent_to")
        self.assertEqual(fingerprint(self.db), before)
        self.db.execute("UPDATE touch SET state = 'bounced', bounced_at = '2026-11-06T10:00:00Z' WHERE touch_id = ?",
                        (sent,))
        self.ok("update_person", {"person_id": self.pat, "email": "new@acme.example"})
        self.assertEqual(self.row(self.pat)["email"], "new@acme.example")

    def test_an_import_touch_counts_as_sent(self):
        seed.touch(self.db, self.pat, origin="import")
        self.assertEqual(self.error("update_person", {"person_id": self.pat, "email": "new@acme.example"})["code"],
                         "email_sent_to")

    def test_a_linkedin_url_is_normalised_and_a_bad_one_refused(self):
        self.ok("update_person", {"person_id": self.pat,
                                  "linkedin_url": "https://www.linkedin.com/in/Pat-Lee/?trk=public"})
        self.assertEqual(self.row(self.pat)["linkedin_url"], "https://www.linkedin.com/in/pat-lee")
        self.assertEqual(self.error("update_person", {"person_id": self.pat, "linkedin_url": "https://x.example/p"})
                         ["code"], "bad_input")

    def test_null_is_refused_for_a_field_that_cannot_be_empty(self):
        self.assertEqual(self.error("update_person", {"person_id": self.pat, "name": None})["code"], "bad_input")

    def test_the_same_call_twice_is_one_event_and_one_item(self):
        first = self.ok("update_person", {"person_id": self.pat, "title": "Staff Engineer"})
        again = self.ok("update_person", {"person_id": self.pat, "title": "Staff Engineer"})
        self.assertEqual(again["changed"], [])
        self.assertEqual(len(self.events("edited")), 1)
        self.assertEqual(len(self.items()), 1)
        self.assertTrue(first["review_id"])

    def test_nothing_sent_writes_nothing(self):
        before = fingerprint(self.db)
        got = self.ok("update_person", {"person_id": self.pat})
        self.assertEqual(got["changed"], [])
        self.assertEqual(fingerprint(self.db), before)

    def test_a_missing_person_is_not_found(self):
        self.assertEqual(self.error("update_person", {"person_id": 9999, "title": "X"})["code"], "not_found")

    def test_a_cleared_email_is_held_by_the_sender_and_the_message_is_untouched(self):
        approved = seed.message(self.db, seed.touch(self.db, self.pat), 0, "approved")
        before = dict(self.db.execute("SELECT * FROM message WHERE message_id = ?", (approved,)).fetchone())
        self.ok("update_person", {"person_id": self.pat, "email": None})
        self.assertEqual(dict(self.db.execute("SELECT * FROM message WHERE message_id = ?", (approved,)).fetchone()),
                         before)
        now = datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        found = {v.message_id: v.reason for v in
                 queue.verdicts(self.db, now, load_send_config(), day=None, gate=None)}
        self.assertEqual(found[approved], "no_address")


if __name__ == "__main__":
    unittest.main()
