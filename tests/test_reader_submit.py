"""The reader's submit: quote checks, refusal, re-queue and duplicates (issue #32, Task 3)."""
import json
import unittest
from dataclasses import replace

from outreach.reader import queue
from outreach.reader.config import load_reader_config
from outreach.reader.submit import SubmitResult, blank_field, submit
from outreach.reader.types import FIELDS, FieldReading
from outreach.store import body_sha256
from tests.ranking_store import decision
from tests.storetest import fresh_store, seed_company, seed_posting

VERSION = "rv-000000000001"
T0 = "2026-10-23T08:00:00Z"
T_SUBMIT = "2026-10-23T08:05:00Z"   # inside the 30 minute lease from T0
T_LATE = "2026-10-23T09:00:00Z"     # after the lease from T0 ran out
T_LATER = "2026-10-23T09:10:00Z"

SPONSOR_LINE = "We do not sponsor visas. Candidates must hold a US work permit."


def body_of(seed: str = SPONSOR_LINE, length: int = 600) -> str:
    filler = " We build tools for small teams and ship every week."
    text = seed
    while len(text) < length:
        text += filler
    return text


def blank_fields() -> dict:
    out = {}
    for name in FIELDS:
        if name in ("years_low", "years_high"):
            out[name] = {"value": None, "quote": ""}
        elif name in ("start_date", "location", "pay"):
            out[name] = {"value": "", "quote": ""}
        else:
            out[name] = {"value": "unknown", "quote": ""}
    return out


class SubmitCase(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = load_reader_config()
        self.company = seed_company(self.conn, "Acme")

    def leased(self, body=None, *, title="Software Engineer", location="Austin, TX", cfg=None, now=T0):
        """A posting with a known body, queued and leased. Returns its ReadRequest."""
        body = body_of() if body is None else body
        pid = seed_posting(self.conn, self.company, title, body, location)
        decision(self.conn, pid)
        self.assertTrue(queue.enqueue(self.conn, pid, cfg=cfg or self.cfg, version=VERSION))
        batch = queue.next_batch(self.conn, 1, cfg=cfg or self.cfg, version=VERSION, now=now)
        self.assertEqual([r.posting_id for r in batch], [pid])
        return batch[0]

    def reading(self, request, *, with_version=False, **overrides) -> dict:
        """A full valid reading for `request`, every field unknown or empty, then `overrides` as (value, quote)."""
        fields = blank_fields()
        for name, (value, quote) in overrides.items():
            fields[name] = {"value": value, "quote": quote}
        obj = {"posting_id": request.posting_id, "body_sha256": request.body_sha256, "fields": fields}
        if with_version:
            obj["reader_version"] = request.reader_version
        return obj

    def submit(self, readings, now=T_SUBMIT, cfg=None):
        return submit(self.conn, readings, cfg=cfg or self.cfg, version=VERSION, now=now)

    def stored(self, posting_id=None):
        sql = "SELECT * FROM posting_reading" + ("" if posting_id is None else " WHERE posting_id = ?")
        return self.conn.execute(sql, () if posting_id is None else (posting_id,)).fetchall()

    def queue_row(self, request):
        return self.conn.execute(
            "SELECT * FROM read_queue WHERE posting_id = ? AND body_sha256 = ? AND reader_version = ?",
            (request.posting_id, request.body_sha256, request.reader_version)).fetchone()

    def events(self):
        return self.conn.execute("SELECT * FROM event WHERE entity = 'reading'").fetchall()

    def decisions(self):
        return [tuple(r) for r in self.conn.execute("SELECT * FROM decision ORDER BY posting_id")]


class StoredTests(SubmitCase):
    def test_valid_reading_is_stored_verified(self):
        req = self.leased()
        before = self.decisions()
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "We do not sponsor visas.")))
        self.assertEqual(res, SubmitResult(req.posting_id, req.body_sha256, "stored"))
        (row,) = self.stored()
        self.assertEqual((row["verified"], row["refused_fields"], row["reader_version"], row["read_at"]),
                         (1, "[]", VERSION, T_SUBMIT))
        fields = json.loads(row["fields"])
        self.assertEqual(fields["reader_version"], VERSION)
        self.assertEqual(fields["fields"]["sponsorship"], {"value": "wont_sponsor", "quote": "We do not sponsor visas."})
        self.assertEqual(self.queue_row(req)["state"], "done")
        self.assertIsNone(self.queue_row(req)["lease_until"])
        (event,) = self.events()
        self.assertEqual((event["kind"], event["actor"], event["entity_id"], event["company_id"]),
                         ("stored", "claude", req.posting_id, self.company))
        detail = json.loads(event["detail"])
        self.assertEqual((detail["posting_id"], detail["reader_version"]), (req.posting_id, VERSION))
        self.assertEqual(self.decisions(), before, "submit never writes a decision")

    def test_a_reading_that_carries_its_version_is_stored_too(self):
        req = self.leased()
        (res,) = self.submit(self.reading(req, with_version=True))
        self.assertEqual(res.status, "stored")

    def test_the_version_defaults_to_the_files_version(self):
        pid = seed_posting(self.conn, self.company, "Software Engineer", body_of(), "Austin, TX")
        decision(self.conn, pid)
        self.assertTrue(queue.enqueue(self.conn, pid, cfg=self.cfg))
        (req,) = queue.next_batch(self.conn, 1, cfg=self.cfg, now=T0)
        (res,) = submit(self.conn, self.reading(req), cfg=self.cfg, now=T_SUBMIT)
        self.assertEqual(res.status, "stored")

    def test_whitespace_and_case_differences_pass(self):
        req = self.leased()
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "we do  NOT sponsor\nvisas")))
        self.assertEqual(res.status, "stored")
        self.assertEqual(self.stored()[0]["verified"], 1)

    def test_short_fields_take_a_one_word_quote(self):
        req = self.leased(body_of(SPONSOR_LINE + " This role is Remote."))
        (res,) = self.submit(self.reading(req, remote=("yes", "Remote"), location=("Austin, TX", "Austin")))
        self.assertEqual(res.status, "stored")

    def test_an_unchecked_quote_on_an_unknown_value_is_dropped(self):
        """verified = 1 means every stored quote was found; a quote the value does not need is not kept unless it
        is found."""
        req = self.leased()
        (res,) = self.submit(self.reading(req, citizenship_or_clearance=("unknown", "must be a citizen of mars"),
                                          sponsorship=("silent", "We do not sponsor visas.")))
        self.assertEqual(res.status, "stored")
        fields = json.loads(self.stored()[0]["fields"])["fields"]
        self.assertEqual(fields["citizenship_or_clearance"], {"value": "unknown", "quote": ""})
        self.assertEqual(fields["sponsorship"], {"value": "silent", "quote": "We do not sponsor visas."})


class RefusalTests(SubmitCase):
    def assert_requeued(self, req, results, field):
        (res,) = results
        self.assertEqual(res.status, "requeued")
        self.assertEqual(res.refused_fields, (field,))
        self.assertEqual(self.stored(), [])
        row = self.queue_row(req)
        self.assertEqual((row["state"], row["lease_until"], row["attempts"]), ("queued", None, 1))
        self.assertIn(field, row["last_error"])
        self.assertEqual(self.events(), [])

    def test_paraphrase_is_refused_and_requeued(self):
        req = self.leased()
        self.assert_requeued(req, self.submit(self.reading(
            req, sponsorship=("wont_sponsor", "no visa sponsorship is offered"))), "sponsorship")

    def test_one_word_quote_is_refused(self):
        req = self.leased()
        self.assert_requeued(req, self.submit(self.reading(req, sponsorship=("wont_sponsor", "sponsor"))),
                             "sponsorship")

    def test_a_quote_cut_inside_a_word_is_refused(self):
        req = self.leased()
        self.assert_requeued(req, self.submit(self.reading(req, sponsorship=("wont_sponsor", "do not sponsor vis"))),
                             "sponsorship")

    def test_unquoted_fact_is_refused(self):
        req = self.leased()
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "")))
        self.assertEqual(res.status, "requeued")
        self.assertIn("sponsorship", res.error)
        self.assertEqual(self.stored(), [])
        row = self.queue_row(req)
        self.assertEqual((row["state"], row["attempts"]), ("queued", 1))
        self.assertIn("sponsorship", row["last_error"])

    def test_requeued_posting_is_handed_out_again_with_one_more_attempt(self):
        req = self.leased()
        self.submit(self.reading(req, sponsorship=("wont_sponsor", "no visa sponsorship is offered")))
        (again,) = queue.next_batch(self.conn, 1, cfg=self.cfg, version=VERSION, now=T_SUBMIT)
        self.assertEqual(again.posting_id, req.posting_id)
        self.assertEqual(self.queue_row(req)["attempts"], 2)

    def test_refusal_at_the_limit_stores_a_partial_reading(self):
        cfg = replace(self.cfg, max_attempts=1)
        req = self.leased(cfg=cfg)
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "no visa sponsorship is offered"),
                                          location=("Austin, TX", "Austin, TX")), cfg=cfg)
        self.assertEqual((res.status, res.refused_fields), ("partial", ("sponsorship",)))
        (row,) = self.stored()
        self.assertEqual((row["verified"], row["refused_fields"]), (0, '["sponsorship"]'))
        fields = json.loads(row["fields"])["fields"]
        self.assertEqual(fields["location"], {"value": "Austin, TX", "quote": "Austin, TX"})
        self.assertEqual(fields["sponsorship"], {"value": "unknown", "quote": ""})
        self.assertEqual(self.queue_row(req)["state"], "refused")
        self.assertIn("sponsorship", self.queue_row(req)["last_error"])
        (event,) = self.events()
        self.assertEqual(event["kind"], "partial")

    def test_partial_blanks_numbers_and_text_by_their_type(self):
        cfg = replace(self.cfg, max_attempts=1)
        req = self.leased(cfg=cfg)
        (res,) = self.submit(self.reading(req, years_low=(2, "two years of shipping code"),
                                          pay=("$150,000", "$150,000"), level=("new_grad", "a new grad role")),
                             cfg=cfg)
        self.assertEqual((res.status, res.refused_fields), ("partial", ("level", "years_low", "pay")))
        fields = json.loads(self.stored()[0]["fields"])["fields"]
        self.assertEqual(fields["years_low"], {"value": None, "quote": ""})
        self.assertEqual(fields["pay"], {"value": "", "quote": ""})
        self.assertEqual(fields["level"], {"value": "unknown", "quote": ""})
        self.assertEqual(json.loads(self.stored()[0]["refused_fields"]), ["level", "years_low", "pay"])

    def test_blank_field_by_type(self):
        self.assertEqual(blank_field("sponsorship"), FieldReading("unknown", ""))
        self.assertEqual(blank_field("years_high"), FieldReading(None, ""))
        self.assertEqual(blank_field("start_date"), FieldReading("", ""))
        with self.assertRaises(ValueError):
            blank_field("wizardry")

    def test_malformed_object_is_refused_without_a_crash(self):
        extra, wizard = self.leased(), self.leased()
        bad_extra = self.reading(extra)
        bad_extra["fields"]["favourite_colour"] = {"value": "blue", "quote": ""}
        bad_wizard = self.reading(wizard, level=("wizard", "Software Engineer"))
        results = self.submit([bad_extra, bad_wizard])
        self.assertEqual([r.status for r in results], ["requeued", "requeued"])
        self.assertIn("favourite_colour", results[0].error)
        self.assertIn("wizard", results[1].error)
        self.assertEqual(self.stored(), [])
        for req in (extra, wizard):
            row = self.queue_row(req)
            self.assertEqual((row["state"], row["attempts"]), ("queued", 1))
            self.assertIn("malformed", row["last_error"])

    def test_malformed_at_the_limit_ends_refused_with_nothing_stored(self):
        cfg = replace(self.cfg, max_attempts=1)
        req = self.leased(cfg=cfg)
        (res,) = self.submit(self.reading(req, level=("wizard", "Software Engineer")), cfg=cfg)
        self.assertEqual(res.status, "refused")
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.queue_row(req)["state"], "refused")

    def test_garbage_never_raises(self):
        req = self.leased()
        results = self.submit(["not a reading", None, 7, {"posting_id": "x"}, {"posting_id": 2 ** 70},
                               {"posting_id": req.posting_id, "body_sha256": "nothex", "fields": {}},
                               {"posting_id": req.posting_id, "body_sha256": req.body_sha256, "fields": "x"}])
        self.assertEqual([r.status for r in results], ["unknown"] * 6 + ["requeued"])
        self.assertIsNone(results[0].posting_id)
        self.assertTrue(all(r.error for r in results))
        self.assertEqual(self.stored(), [])

    def test_an_input_that_is_not_a_list_or_object_gives_one_result(self):
        (res,) = self.submit("nonsense")
        self.assertEqual(res.status, "unknown")

    def test_unknown_key_is_refused(self):
        pid = seed_posting(self.conn, self.company, "Software Engineer", body_of(), "Austin, TX")
        never = self.reading(type("R", (), {"posting_id": pid, "body_sha256": body_sha256(body_of())})())
        (res,) = self.submit(never)
        self.assertEqual(res.status, "unknown")
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM read_queue").fetchone()[0], 0)

    def test_a_reading_under_another_version_is_unknown(self):
        req = self.leased()
        obj = self.reading(req)
        obj["reader_version"] = "rv-ffffffffffff"
        (res,) = self.submit(obj)
        self.assertEqual(res.status, "unknown")
        self.assertEqual(self.queue_row(req)["state"], "leased")


class QuoteTextTests(SubmitCase):
    def test_title_and_location_count_as_quote_text_for_every_field(self):
        body = body_of("Join a small team that ships often.")
        req = self.leased(body, title="Software Engineer, New Grad", location="Remote, United States")
        quote = "Software Engineer, New Grad"
        (res,) = self.submit(self.reading(req, level=("new_grad", quote), engineering=("yes", quote),
                                          sponsorship=("wont_sponsor", quote),
                                          student_or_grad_window=("yes", quote),
                                          location=("Remote, United States", "Remote, United States"),
                                          remote=("yes", "Remote")))
        self.assertEqual(res.status, "stored")

        other = self.leased(body, title="Software Engineer, New Grad", location="Remote, United States")
        (res,) = self.submit(self.reading(other, level=("new_grad", "Senior Staff Engineer role")))
        self.assertEqual((res.status, res.refused_fields), ("requeued", ("level",)))


class StaleAndDuplicateTests(SubmitCase):
    def test_stale_body_is_not_stored_and_the_new_body_is_queued(self):
        req = self.leased()
        new_body = body_of("We sponsor visas for this role and many others.")
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?", (new_body, req.posting_id))
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "We do not sponsor visas.")))
        self.assertEqual(res.status, "stale")
        self.assertEqual(self.stored(), [])
        old = self.queue_row(req)
        self.assertEqual((old["state"], old["last_error"], old["lease_until"]), ("done", "body changed", None))
        new = self.conn.execute("SELECT * FROM read_queue WHERE posting_id = ? AND body_sha256 = ?",
                                (req.posting_id, body_sha256(new_body))).fetchone()
        self.assertIsNotNone(new)
        self.assertEqual((new["state"], new["reader_version"]), ("queued", VERSION))

    def test_a_deleted_body_is_stale_and_ends_the_row(self):
        req = self.leased()
        self.conn.execute("DELETE FROM posting_body WHERE posting_id = ?", (req.posting_id,))
        (res,) = self.submit(self.reading(req))
        self.assertEqual(res.status, "stale")
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.queue_row(req)["state"], "refused")
        self.assertEqual(self.queue_row(req)["last_error"], "no stored body")

    def test_second_submit_is_a_duplicate(self):
        req = self.leased()
        good = self.reading(req, sponsorship=("wont_sponsor", "We do not sponsor visas."))
        self.assertEqual(self.submit(good)[0].status, "stored")
        row_before = tuple(self.queue_row(req))
        (res,) = self.submit(good)
        self.assertEqual(res.status, "duplicate")
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(tuple(self.queue_row(req)), row_before)

    def test_a_bad_reading_after_a_stored_one_changes_nothing(self):
        req = self.leased()
        self.submit(self.reading(req, sponsorship=("wont_sponsor", "We do not sponsor visas.")))
        row_before = tuple(self.queue_row(req))
        (res,) = self.submit(self.reading(req, sponsorship=("wont_sponsor", "no visa sponsorship is offered")))
        self.assertEqual(res.status, "duplicate")
        (stored,) = self.stored()
        self.assertEqual(stored["verified"], 1)
        self.assertEqual(tuple(self.queue_row(req)), row_before)

    def test_a_submit_for_a_refused_row_is_a_duplicate(self):
        cfg = replace(self.cfg, max_attempts=1)
        req = self.leased(cfg=cfg)
        self.submit(self.reading(req, level=("wizard", "x y z")), cfg=cfg)
        (res,) = self.submit(self.reading(req), cfg=cfg)
        self.assertEqual(res.status, "duplicate")
        self.assertEqual(self.stored(), [])

    def test_one_bad_reading_does_not_undo_the_others(self):
        good, stale = self.leased(), self.leased()
        self.conn.execute("UPDATE posting_body SET body = ? WHERE posting_id = ?",
                          (body_of("Something else entirely now."), stale.posting_id))
        results = self.submit([self.reading(good, sponsorship=("wont_sponsor", "We do not sponsor visas.")),
                               self.reading(stale)])
        self.assertEqual([r.status for r in results], ["stored", "stale"])
        self.assertEqual([r["posting_id"] for r in self.stored()], [good.posting_id])


class LeaseTests(SubmitCase):
    """The queue has no owner column: a reading is keyed on the body and the version, so any reader's valid reading
    of that key is the same fact. The first one is stored, every later one is a duplicate."""

    def hand_on(self, req):
        """The first lease runs out and a second reader takes the posting."""
        (second,) = queue.next_batch(self.conn, 1, cfg=self.cfg, version=VERSION, now=T_LATE)
        self.assertEqual((second.posting_id, second.body_sha256), (req.posting_id, req.body_sha256))
        self.assertEqual(self.queue_row(req)["attempts"], 2)
        return second

    def good(self, req):
        return self.reading(req, sponsorship=("wont_sponsor", "We do not sponsor visas."))

    def test_late_first_reader_after_the_second_finished_is_a_duplicate(self):
        first = self.leased()
        second = self.hand_on(first)
        self.assertEqual(self.submit(self.good(second), now=T_LATE)[0].status, "stored")
        row_before = tuple(self.queue_row(first))
        for late in (self.good(first), self.reading(first, sponsorship=("wont_sponsor", "no visa sponsorship"))):
            (res,) = self.submit(late, now=T_LATER)
            self.assertEqual(res.status, "duplicate")
        self.assertEqual(len(self.stored()), 1)
        self.assertEqual(tuple(self.queue_row(first)), row_before)

    def test_late_first_reader_before_the_second_finishes_stores_once(self):
        first = self.leased()
        second = self.hand_on(first)
        self.assertEqual(self.submit(self.good(first), now=T_LATER)[0].status, "stored")
        (res,) = self.submit(self.good(second), now=T_LATER)
        self.assertEqual(res.status, "duplicate")
        self.assertEqual(len(self.stored()), 1)
        row = self.queue_row(first)
        self.assertEqual((row["state"], row["attempts"]), ("done", 2))

    def test_a_refusal_never_counts_an_attempt(self):
        first = self.leased()
        self.hand_on(first)
        (res,) = self.submit(self.reading(first, sponsorship=("wont_sponsor", "no visa sponsorship")), now=T_LATER)
        self.assertEqual(res.status, "requeued")
        row = self.queue_row(first)
        self.assertEqual((row["state"], row["attempts"], row["lease_until"]), ("queued", 2, None))

    def test_an_expired_lease_nobody_took_still_accepts_a_reading(self):
        first = self.leased()
        (res,) = self.submit(self.good(first), now=T_LATE)
        self.assertEqual(res.status, "stored")
        self.assertEqual(self.queue_row(first)["state"], "done")

    def test_a_reading_for_a_queued_row_is_accepted(self):
        first = self.leased()
        queue.release(self.conn, [first])
        self.assertEqual(self.queue_row(first)["state"], "queued")
        (res,) = self.submit(self.good(first))
        self.assertEqual(res.status, "stored")


if __name__ == "__main__":
    unittest.main()
