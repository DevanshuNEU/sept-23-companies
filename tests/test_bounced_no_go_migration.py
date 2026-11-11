"""Migration 0011 (#224): people whose email already bounced go on the no-go list, once, and nobody else does."""
import unittest

from outreach.store.migrate import MIGRATIONS_DIR
from tests.sendtest import MON, bounce, seed_email_person, seed_sent
from tests.storetest import fresh_store, seed_company

SQL = (MIGRATIONS_DIR / "0011_bounced_no_go.sql").read_text()


class BouncedNoGoMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.company = seed_company(self.db)

    def bounced(self, name, email):
        person = seed_email_person(self.db, self.company, name, email)
        seed_sent(self.db, person, at=MON)
        bounce(self.db, self.db.execute("SELECT touch_id FROM touch WHERE person_id = ?", (person,)).fetchone()[0], MON)
        return person

    def rows(self, person):
        return self.db.execute("SELECT * FROM no_go WHERE person_id = ?", (person,)).fetchall()

    def test_a_bounced_person_goes_on_the_list_once(self):
        person = self.bounced("A", "a@acme.co")
        self.db.executescript(SQL)
        self.db.executescript(SQL)
        (row,) = self.rows(person)
        self.assertEqual((row["reason"], row["source"], row["lifted_at"]), ("other", "rules", None))
        self.assertIn("a@acme.co", row["note"])

    def test_a_person_who_did_not_bounce_is_left_out(self):
        fine = seed_email_person(self.db, self.company, "B", "b@acme.co")
        seed_sent(self.db, fine, at=MON)
        self.db.executescript(SQL)
        self.assertEqual(self.rows(fine), [])

    def test_a_cleared_address_stays_out(self):
        person = self.bounced("C", "c@acme.co")
        self.db.execute("UPDATE person SET email = NULL, channel = 'linkedin', linkedin_reason = 'no_email' "
                        "WHERE person_id = ?", (person,))
        self.db.executescript(SQL)
        self.assertEqual(self.rows(person), [])

    def test_a_lifted_no_go_is_not_added_again(self):
        person = self.bounced("D", "d@acme.co")
        self.db.execute("INSERT INTO no_go(person_id, reason, source, created_at, lifted_at, lift_reason) VALUES "
                        "(?, 'said_no', 'devanshu', '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z', 'ok')", (person,))
        self.db.executescript(SQL)
        self.assertEqual(len(self.rows(person)), 1)


if __name__ == "__main__":
    unittest.main()
