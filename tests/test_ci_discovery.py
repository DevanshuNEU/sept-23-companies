"""
`unittest discover -s tests -t .` imports a test module only when every folder between `tests/` and the module has an
`__init__.py`. A folder without one is skipped without a word, so its tests never run in CI and nothing fails. This
guard turns that silence into a failure that names the file and the missing `__init__.py`.
"""
import pathlib
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
UNITTEST_PATTERN = "test*.py"  # discover's default file pattern


def unreachable(tests_dir):
    """Every test module under `tests_dir` that discover would not import, as (module, missing __init__.py) pairs."""
    tests_dir = pathlib.Path(tests_dir)
    missing = []
    if not (tests_dir / "__init__.py").is_file():
        missing.append((tests_dir, tests_dir / "__init__.py"))
    for module in sorted(tests_dir.rglob(UNITTEST_PATTERN)):
        if "__pycache__" in module.parts:
            continue
        folder = module.parent
        while folder != tests_dir:
            if not (folder / "__init__.py").is_file():
                missing.append((module, folder / "__init__.py"))
                break
            folder = folder.parent
    return missing


class CiDiscovery(unittest.TestCase):
    def test_every_test_module_is_reachable_by_discover(self):
        root = TESTS.parent
        problems = [f"{module.relative_to(root)} is never run: add {init.relative_to(root)}"
                    for module, init in unreachable(TESTS)]
        self.assertEqual([], problems, "\n" + "\n".join(problems))

    def test_the_check_finds_a_folder_without_an_init_and_passes_a_complete_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests = pathlib.Path(tmp) / "tests"
            (tests / "ok").mkdir(parents=True)
            (tests / "lost" / "deeper").mkdir(parents=True)
            for init in (tests / "__init__.py", tests / "ok" / "__init__.py", tests / "lost" / "deeper" / "__init__.py"):
                init.write_text("")
            (tests / "test_top.py").write_text("")
            (tests / "ok" / "test_in_ok.py").write_text("")
            self.assertEqual([], unreachable(tests))
            (tests / "lost" / "deeper" / "test_buried.py").write_text("")
            self.assertEqual([(tests / "lost" / "deeper" / "test_buried.py", tests / "lost" / "__init__.py")],
                             unreachable(tests))

    def test_the_check_notices_a_missing_tests_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            tests = pathlib.Path(tmp) / "tests"
            tests.mkdir()
            self.assertEqual([(tests, tests / "__init__.py")], unreachable(tests))


if __name__ == "__main__":
    unittest.main()
