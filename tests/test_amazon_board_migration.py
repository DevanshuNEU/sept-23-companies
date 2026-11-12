"""Migration 0013: the amazon board the import left switched off is switched on, as a never-read board."""
import unittest

from outreach.store.migrate import MIGRATIONS_DIR
from tests.storetest import fresh_store, seed_board, seed_company

SQL = (MIGRATIONS_DIR / "0013_amazon_board_on.sql").read_text()


class AmazonBoardMigrationTest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_store()
        self.addCleanup(self.db.close)

    def board(self, ats, token, active):
        cid = seed_company(self.db, f"{ats} co")
        bid = seed_board(self.db, cid, ats=ats, token=token)
        self.db.execute("UPDATE board SET active = ? WHERE board_id = ?", (active, bid))
        return bid

    def row(self, bid):
        return self.db.execute("SELECT * FROM board WHERE board_id = ?", (bid,)).fetchone()

    def test_the_amazon_board_is_switched_on_and_nothing_else(self):
        amazon = self.board("amazon", "amazon", 0)
        other = self.board("greenhouse", "acme", 0)
        self.db.executescript(SQL)
        self.assertEqual((self.row(amazon)["active"], self.row(amazon)["last_read"]), (1, None))
        self.assertEqual(self.row(other)["active"], 0)

    def test_running_it_twice_changes_nothing_more(self):
        amazon = self.board("amazon", "amazon", 0)
        self.db.executescript(SQL)
        self.db.execute("UPDATE board SET next_read_at = '2026-11-13T00:00:00Z' WHERE board_id = ?", (amazon,))
        self.db.executescript(SQL)
        self.assertEqual(self.row(amazon)["next_read_at"], "2026-11-13T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
