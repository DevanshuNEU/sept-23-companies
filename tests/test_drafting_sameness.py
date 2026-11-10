"""The sameness module (#203): his first emails read as one corpus, and what repeats across them."""
import unittest

from outreach.drafting.sameness import (Corpus, evidence_used, first_sentence, load_corpus, opening_count, overused,
                                        recent_openings, recent_subjects, reused, sentences)
from tests import apiseed as seed
from tests.storetest import fresh_store

SIG = "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com"  # the fallback signature (tests never read the real one)


def email(*lines: str, greeting: str = "Hi Pat,") -> str:
    return greeting + "\n\n" + "\n\n".join(lines) + "\n\nThanks,\n" + SIG


class TextTest(unittest.TestCase):
    def test_sentences_skip_greeting_signature_and_short_ones(self):
        body = email("I built a code search service for coding agents. It works.", "Who should I write to about it?")
        self.assertEqual(sentences(body), ["i built a code search service for coding agents",
                                           "who should i write to about it"])

    def test_empty_body_has_no_sentences(self):
        self.assertEqual(sentences("Hi Pat,\n\nThanks,\n" + SIG), [])
        self.assertEqual(first_sentence("Hi Pat,\n\nThanks,\n" + SIG), "")

    def test_first_sentence_is_after_the_greeting(self):
        self.assertEqual(first_sentence(email("I graduated from Northeastern in May 2026. Then more.")),
                         "i graduated from northeastern in may 2026")
        self.assertEqual(first_sentence(email("Short.")), "short")


class CorpusTest(unittest.TestCase):
    def test_reused_counts_other_emails_not_repeats_inside_one(self):
        line = "I graduated from Northeastern in May 2026."
        corpus = Corpus(bodies=(email(line, line), email(line), email("Something else entirely here, I promise.")),
                        subjects=("a", "b", "c"))
        self.assertEqual(reused(email(line), corpus, min_count=2), [("i graduated from northeastern in may 2026", 2)])
        self.assertEqual(reused(email(line), corpus, min_count=3), [])

    def test_opening_count_uses_first_four_words_of_last_100(self):
        same = email("I graduated from Northeastern and then built a thing.")
        other = email("The first time I added a reranker, search got worse.")
        corpus = Corpus(bodies=(same,) * 5 + (other,) * 100 + (same,) * 3, subjects=("s",) * 108)
        self.assertEqual(opening_count(email("I graduated from Northeastern in May."), corpus), 5)
        self.assertEqual(opening_count(email("I graduated from Northeastern in May."), corpus, last=4), 4)

    def test_overused_lists_sentences_and_runs_seen_10_times(self):
        corpus = Corpus(bodies=tuple(email(f"I graduated from Northeastern in May 2026 and number {n} follows.")
                                     for n in range(10)), subjects=("s",) * 10)
        texts = [o["text"] for o in overused(corpus, min_count=10)]
        self.assertIn("i graduated from northeastern in may", texts)
        self.assertTrue(all(o["count"] == 10 for o in overused(corpus, min_count=10)))
        self.assertEqual(overused(corpus, min_count=11), [])

    def test_recent_openings_and_subjects_are_newest_first(self):
        corpus = Corpus(bodies=(email("Newest opening line is this one."), email("Older opening line is this one.")),
                        subjects=("newest", "older"))
        self.assertEqual(recent_openings(corpus, n=1), ["newest opening line is this one"])
        self.assertEqual(recent_subjects(corpus), ["newest", "older"])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.acme = seed.company(self.conn, "Acme", "working")

    def first_email(self, name: str, body: str, state: str = "drafted", subject: str = "subj") -> int:
        pid = seed.person(self.conn, self.acme, name)
        return seed.message(self.conn, seed.touch(self.conn, pid), 0, state, subject=subject, body=body)

    def test_load_corpus_excludes_the_message_and_newest_first(self):
        a = self.first_email("Ana", email("Body of the first email goes here."), subject="first")
        self.first_email("Ben", email("Body of the second email goes here."), subject="second")
        self.assertEqual(load_corpus(self.conn, exclude_message_id=None).subjects, ("second", "first"))
        self.assertEqual(load_corpus(self.conn, exclude_message_id=a).subjects, ("second",))

    def test_unsaved_draft_counts_every_email(self):
        for n in range(3):
            self.first_email(f"P{n}", email("I graduated from Northeastern in May 2026."))
        corpus = load_corpus(self.conn, exclude_message_id=None)
        self.assertEqual(reused(email("I graduated from Northeastern in May 2026."), corpus), [
            ("i graduated from northeastern in may 2026", 3)])

    def test_skipped_and_planned_do_not_count(self):
        mid = self.first_email("Ana", email("A skipped email body is here."))
        self.conn.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        pid = seed.person(self.conn, self.acme, "Ben")
        touch = seed.touch(self.conn, pid)
        self.conn.execute("INSERT INTO message(touch_id, seq, state, created_at, updated_at) VALUES (?, 0, 'planned', ?, ?)",
                          (touch, seed.T0, seed.T0))
        follow = seed.message(self.conn, seed.touch(self.conn, seed.person(self.conn, self.acme, "Cy")), 0, "sent",
                              body=email("A sent first email body."))
        self.assertEqual(len(load_corpus(self.conn, exclude_message_id=None).bodies), 1)
        self.assertTrue(follow)

    def test_evidence_used_counts_projects_since(self):
        for n, (project, at) in enumerate([("Overhear", "2026-11-09T12:00:00Z"), ("Overhear", "2026-11-01T12:00:00Z"),
                                           ("OpenCodeIntel", "2026-10-01T12:00:00Z")]):
            mid = self.first_email(f"P{n}", email("Body text for this one is here."))
            self.conn.execute("UPDATE message SET project = ?, created_at = ? WHERE message_id = ?", (project, at, mid))
        self.assertEqual(evidence_used(self.conn, since="2026-10-27T00:00:00Z"), {"Overhear": 2})


if __name__ == "__main__":
    unittest.main()
