"""The API's import boundary: third-party code lives in outreach/api and nowhere else, with one exception.

Everything else in `outreach/` stays standard library so the sender and the crawler start without the API's
packages, and none of them can reach the one approve route. The exception (#13 ruling, lean v1): `outreach/mcp` is
built on the official MCP SDK, so it may import `mcp`, `mcp_types` and `pydantic`, and nothing else third party.
"""
import ast
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTREACH = "outreach"
ALLOWED_OUTSIDE_API = set(sys.stdlib_module_names) | {"outreach", "live_checkout"}
ALLOWED_IN_MCP = ALLOWED_OUTSIDE_API | {"mcp", "mcp_types", "pydantic"}
OLD_MAILER_PACKAGES = {"mail", "refresh", "dashboard"}
PINNED = re.compile(r"^[A-Za-z0-9_.-]+==[0-9]+(\.[0-9]+)+$")


def imports_of(path: Path, package: str) -> list[str]:
    """Absolute dotted names a file imports. `package` is the file's own package, for relative imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.append(module)
            # `from outreach import api` imports outreach.api
            names.extend(f"{module}.{alias.name}" for alias in node.names)
    return names


def package_of(root: Path, path: Path) -> str:
    # A module's package is its folder; a package's own __init__ is that package.
    parts = list(path.relative_to(root).with_suffix("").parts)[:-1]
    return ".".join(parts)


def python_files(root: Path, *, inside_api: bool):
    api = root / OUTREACH / "api"
    for path in sorted((root / OUTREACH).rglob("*.py")):
        if (api in path.parents) == inside_api:
            yield path


def third_party_outside_api(root: Path) -> list[str]:
    found = []
    mcp = root / OUTREACH / "mcp"
    for path in python_files(root, inside_api=False):
        allowed = ALLOWED_IN_MCP if mcp in path.parents else ALLOWED_OUTSIDE_API
        tops = {name.split(".")[0] for name in imports_of(path, package_of(root, path)) if name}
        found.extend(f"{path.relative_to(root)} imports {top}" for top in sorted(tops - allowed))
    return found


def api_imports_outside_api(root: Path) -> list[str]:
    found = []
    for path in python_files(root, inside_api=False):
        for name in imports_of(path, package_of(root, path)):
            if name == "outreach.api" or name.startswith("outreach.api."):
                found.append(f"{path.relative_to(root)} imports {name}")
    return found


class StdlibBoundaryTest(unittest.TestCase):
    def test_outreach_outside_api_imports_only_the_standard_library(self):
        self.assertEqual(third_party_outside_api(ROOT), [])

    def test_the_scan_reports_a_planted_third_party_import(self):
        # Without this the scan above could pass because it found nothing to look at.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            shutil.copytree(ROOT / OUTREACH, copy / OUTREACH, ignore=shutil.ignore_patterns("__pycache__"))
            (copy / OUTREACH / "store" / "planted.py").write_text("import fastapi\n", encoding="utf-8")
            (copy / OUTREACH / "planted_from.py").write_text("from pydantic import BaseModel\n", encoding="utf-8")
            (copy / OUTREACH / "api" / "fine.py").write_text("import fastapi\n", encoding="utf-8")
            (copy / OUTREACH / "mcp" / "fine.py").write_text("import mcp\nfrom pydantic import Field\n",
                                                            encoding="utf-8")
            (copy / OUTREACH / "mcp" / "planted.py").write_text("import httpx\n", encoding="utf-8")
            found = third_party_outside_api(copy)
        self.assertEqual(
            found,
            ["outreach/mcp/planted.py imports httpx", "outreach/planted_from.py imports pydantic",
             "outreach/store/planted.py imports fastapi"],
        )

    def test_the_scan_reads_something(self):
        self.assertGreater(len(list(python_files(ROOT, inside_api=False))), 20)
        self.assertGreater(len(list(python_files(ROOT, inside_api=True))), 3)

    def test_nothing_outside_api_imports_outreach_api(self):
        # Covers outreach/mcp, outreach/sending and outreach/crawl: there is no approve path for Claude.
        self.assertEqual(api_imports_outside_api(ROOT), [])

    def test_the_api_import_scan_catches_every_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            shutil.copytree(ROOT / OUTREACH, copy / OUTREACH, ignore=shutil.ignore_patterns("__pycache__"))
            (copy / OUTREACH / "sending" / "a.py").write_text("import outreach.api.app\n", encoding="utf-8")
            (copy / OUTREACH / "sending" / "b.py").write_text("from outreach.api import app\n", encoding="utf-8")
            (copy / OUTREACH / "sending" / "c.py").write_text("from .. import api\n", encoding="utf-8")
            (copy / OUTREACH / "sending" / "d.py").write_text("def f():\n    from ..api.app import create_app\n", encoding="utf-8")
            found = api_imports_outside_api(copy)
        files = {line.split(" ")[0] for line in found}
        self.assertEqual(
            files,
            {f"outreach/sending/{name}.py" for name in "abcd"},
        )

    def test_api_never_imports_the_old_mailer_packages(self):
        found = []
        for path in python_files(ROOT, inside_api=True):
            for name in imports_of(path, package_of(ROOT, path)):
                if name.split(".")[0] in OLD_MAILER_PACKAGES:
                    found.append(f"{path.relative_to(ROOT)} imports {name}")
        self.assertEqual(found, [])

    def test_the_pin_pattern_accepts_exact_versions_only(self):
        for good in ("fastapi==0.161.1", "pydantic==2.14.1", "uvicorn==0.54.0"):
            self.assertRegex(good, PINNED)
        for bad in ("fastapi==", "fastapi==*", "fastapi==latest", "fastapi==0.161.*", "fastapi>=0.1", "fastapi", "fastapi==1"):
            self.assertNotRegex(bad, PINNED)

    def test_runtime_dependencies_stay_empty(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["dependencies"], [])
        group = project["dependency-groups"]["api"]
        self.assertTrue(group)
        for entry in group:
            self.assertRegex(entry, PINNED, f"{entry} is not pinned to an exact version with ==")
        self.assertEqual(project["dependency-groups"]["mcp"], ["mcp==2.3.0"])  # the SDK the MCP server runs on (#13)
        self.assertEqual(project["tool"]["uv"]["default-groups"], ["api", "mcp"])


if __name__ == "__main__":
    unittest.main()
