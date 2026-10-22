"""The shared write unit: a transaction of its own, or a savepoint in the caller's."""
import unittest

from outreach.sources.base import describe_error as base_describe_error
from outreach.store import atomic, describe_error, transaction
from tests.storetest import fresh_store


def count(conn):
    return conn.execute("SELECT count(*) FROM company").fetchone()[0]


def add(conn, name):
    conn.execute("INSERT INTO company(name, status_at, created_at) VALUES (?, '2026-10-19T12:00:00Z', "
                 "'2026-10-19T12:00:00Z')", (name,))


class AtomicTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()

    def test_opens_and_commits_its_own_transaction(self):
        with atomic(self.conn, "unit"):
            self.assertTrue(self.conn.in_transaction)
            add(self.conn, "Acme")
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(count(self.conn), 1)

    def test_rolls_back_its_own_transaction_on_error(self):
        with self.assertRaises(ZeroDivisionError):
            with atomic(self.conn, "unit"):
                add(self.conn, "Acme")
                1 / 0
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(count(self.conn), 0)

    def test_inside_a_transaction_a_failure_undoes_only_the_unit(self):
        with transaction(self.conn):
            add(self.conn, "Before")
            with self.assertRaises(ZeroDivisionError):
                with atomic(self.conn, "unit"):
                    add(self.conn, "Inside")
                    1 / 0
            self.assertTrue(self.conn.in_transaction)
            add(self.conn, "After")
        self.assertEqual([r[0] for r in self.conn.execute("SELECT name FROM company ORDER BY company_id")],
                         ["Before", "After"])

    def test_inside_a_transaction_success_keeps_the_work_for_the_caller(self):
        with self.assertRaises(ZeroDivisionError):
            with transaction(self.conn):
                with atomic(self.conn, "unit"):
                    add(self.conn, "Acme")
                1 / 0
        self.assertEqual(count(self.conn), 0)

    def test_the_original_error_wins_when_sqlite_already_rolled_back(self):
        self.conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(ZeroDivisionError):
            with atomic(self.conn, "unit"):
                add(self.conn, "Acme")
                self.conn.execute("ROLLBACK")
                1 / 0
        self.assertFalse(self.conn.in_transaction)

    def test_a_name_that_is_not_an_identifier_is_refused(self):
        with self.assertRaises(ValueError):
            with atomic(self.conn, "x; DROP TABLE company"):
                pass

    def test_describe_error_is_one_function(self):
        self.assertIs(base_describe_error, describe_error)
        self.assertEqual(describe_error(TypeError("text")), "TypeError: text")


if __name__ == "__main__":
    unittest.main()
