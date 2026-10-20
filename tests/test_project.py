import os
import re
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXACT_PIN = re.compile(r"^[A-Za-z0-9_.\[\],-]+==[0-9][^,;<>~!=]*$")


def load_pyproject():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


class Project(unittest.TestCase):
    def test_outreach_imports(self):
        import outreach  # noqa: F401

    def test_python_is_pinned_to_314(self):
        self.assertEqual(load_pyproject()["project"]["requires-python"], "==3.14.*")

    def test_every_dependency_is_an_exact_pin(self):
        data = load_pyproject()
        entries = list(data["project"]["dependencies"])
        for group in data.get("dependency-groups", {}).values():
            entries.extend(group)
        for entry in entries:
            self.assertRegex(entry, EXACT_PIN)

    def test_uv_uses_system_python_and_builds_nothing(self):
        uv = load_pyproject()["tool"]["uv"]
        self.assertIs(uv["package"], False)
        self.assertEqual(uv["python-preference"], "only-system")

    def test_lock_file_is_committed(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "uv.lock")))

    def test_old_code_does_not_import_outreach(self):
        pattern = re.compile(r"^\s*(import outreach|from outreach)\b", re.M)
        for folder in ("mail", "refresh", "dashboard"):
            for dirpath, _dirs, files in os.walk(os.path.join(ROOT, folder)):
                for name in files:
                    if not name.endswith((".py", ".js", ".sh")):
                        continue
                    path = os.path.join(dirpath, name)
                    with open(path, encoding="utf-8", errors="replace") as f:
                        self.assertIsNone(pattern.search(f.read()), path)
