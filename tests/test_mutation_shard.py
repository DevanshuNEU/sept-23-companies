"""
`tests/mutation_check.py --shard I/N` runs the entries whose zero-based index % N == I, so a CI matrix can split the
full run across jobs. Every entry lands in exactly one shard, `--list` still shows all of them, and a shard fails
red the same way the whole run does (a missed or skipped entry).
"""
import argparse
import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest import mock

from tests import mutation_check as mc


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mc.main(argv)
    return code, out.getvalue()


class ParseShard(unittest.TestCase):
    def test_i_over_n(self):
        self.assertEqual((0, 6), mc.parse_shard("0/6"))
        self.assertEqual((5, 6), mc.parse_shard("5/6"))
        self.assertEqual((0, 1), mc.parse_shard("0/1"))

    def test_a_bad_shard_is_refused(self):
        for bad in ("", "3", "a/b", "1/", "/3", "1/2/3", "-1/3", "3/3", "7/6", "0/0", "1.5/3", " 1/3x"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                mc.parse_shard(bad)


class SelectShard(unittest.TestCase):
    def test_index_mod_n_equals_i(self):
        items = list("abcdefghij")
        self.assertEqual(list("adgj"), mc.select_shard(items, (0, 3)))
        self.assertEqual(list("beh"), mc.select_shard(items, (1, 3)))
        self.assertEqual(list("cfi"), mc.select_shard(items, (2, 3)))

    def test_shards_cover_every_entry_exactly_once(self):
        for n in (1, 2, 6, 7, 200):
            with self.subTest(n=n):
                parts = [mc.select_shard(list(range(len(mc.MUTATIONS))), (i, n)) for i in range(n)]
                self.assertEqual(list(range(len(mc.MUTATIONS))), sorted(x for part in parts for x in part))

    def test_no_shard_selects_everything(self):
        self.assertEqual([1, 2, 3], mc.select_shard([1, 2, 3], None))


class ShardRun(unittest.TestCase):
    """main() on five small entries over temp files, with the suite replaced by a stub."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="mutation-shard-test-"))
        self.files = []
        entries = []
        for n in range(5):
            path = self.tmp / f"f{n}.py"
            path.write_text(f"VALUE = {n}\n")
            self.files.append(path)
            entries.append((path, f"entry {n}", f"VALUE = {n}", f"VALUE = {n + 100}", "test_x"))
        self.entries = entries
        self.suite_runs = []
        self.suite_result = (1, ["FAIL: test_x (tests.test_a.A.test_x)"], False)
        for patcher in (mock.patch.object(mc, "MUTATIONS", entries),
                        mock.patch.object(mc.live_checkout, "is_live", return_value=False),
                        mock.patch.object(mc, "dirty_files", return_value=[]),
                        mock.patch.object(mc, "run_suite", side_effect=self.fake_suite),
                        mock.patch.object(mc.signal, "signal")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_suite(self):
        """The first call is the baseline (green). Later calls see which file is mutated right now."""
        if not self.suite_runs:
            self.suite_runs.append("baseline")
            return 0, [], False
        mutated = [p.name for n, p in enumerate(self.files) if p.read_text() != f"VALUE = {n}\n"]
        self.suite_runs.append(mutated)
        return self.suite_result

    def applied(self):
        return [r[0] for r in self.suite_runs[1:]]

    def test_a_shard_runs_only_its_entries_and_keeps_the_global_numbers(self):
        code, out = run_main(["--shard", "1/2"])
        self.assertEqual(0, code)
        self.assertEqual(["f1.py", "f3.py"], self.applied())
        self.assertIn("caught 2  missed 0  skipped 0", out)
        self.assertIn(" 2 f1.py: entry 1", out)
        self.assertIn(" 4 f3.py: entry 3", out)
        self.assertNotIn("entry 0", out)
        self.assertIn("2 mutations to apply", out)

    def test_the_whole_run_is_unchanged_without_a_shard(self):
        code, out = run_main([])
        self.assertEqual(0, code)
        self.assertEqual(5, len(self.applied()))
        self.assertIn("caught 5  missed 0  skipped 0", out)

    def test_list_prints_every_entry_even_with_a_shard(self):
        code, out = run_main(["--list", "--shard", "0/3"])
        self.assertEqual(0, code)
        self.assertEqual(5, len([l for l in out.splitlines() if "entry " in l]))
        self.assertEqual([], self.suite_runs)

    def test_a_missed_entry_in_a_shard_fails_red(self):
        self.suite_result = (0, [], False)
        code, out = run_main(["--shard", "0/5"])
        self.assertEqual(1, code)
        self.assertIn("MISSED", out)
        self.assertIn("caught 0  missed 1  skipped 0", out)

    def test_a_skipped_entry_in_a_shard_fails_red(self):
        self.files[2].write_text("VALUE = moved\n")
        code, out = run_main(["--shard", "2/5"])
        self.assertEqual(1, code)
        self.assertIn("SKIP", out)
        self.assertIn("caught 0  missed 0  skipped 1", out)

    def test_an_entry_in_another_shard_is_not_checked_for_a_moved_anchor(self):
        self.files[2].write_text("VALUE = moved\n")
        code, out = run_main(["--shard", "0/5"])
        self.assertEqual(0, code)
        self.assertNotIn("SKIP", out)

    def test_the_files_are_restored_after_a_shard_run(self):
        run_main(["--shard", "0/2"])
        self.assertEqual([f"VALUE = {n}\n" for n in range(5)], [p.read_text() for p in self.files])


if __name__ == "__main__":
    unittest.main()
