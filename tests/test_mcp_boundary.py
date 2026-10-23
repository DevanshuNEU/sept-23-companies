"""The rules every file in outreach/mcp keeps: it never imports the API, the sender, the old mailer or the network
helpers, never names a port, a loopback address or an API route, never uses OR REPLACE or OR IGNORE, and has no
em or en dash. The import rule is checked in the source here (ast); test_mcp_server checks what a fresh process loads."""
import ast
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = ROOT / "outreach" / "mcp"

FORBIDDEN_IMPORTS = ("outreach.api", "outreach.sending", "outreach.net", "mail", "refresh", "dashboard", "gmail")
FORBIDDEN_TEXT = ("3001", "3002", "3011", "3013", "3100", "5173", "127.0.0.1", "localhost", "/api/",
                  "Idempotency-Key")
DASHES = ("–", "—")


def sources(folder: Path = MCP_DIR) -> list[Path]:
    return sorted(folder.rglob("*.py"))


def imported_names(path: Path, package: str | None = None) -> list[str]:
    """Every module a file imports, absolute names (a relative import is resolved against the package)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if package is None:
        package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.append(module)
            names.extend(f"{module}.{alias.name}" for alias in node.names)
    return names


def forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN_IMPORTS)


class McpBoundaryTest(unittest.TestCase):
    def test_the_package_exists_and_has_files_to_scan(self):
        self.assertTrue((MCP_DIR / "__init__.py").exists())
        self.assertGreater(len(sources()), 0)

    def test_mcp_source_imports_nothing_that_sends_or_serves(self):
        for path in sources():
            bad = [n for n in imported_names(path) if forbidden(n)]
            self.assertEqual(bad, [], path)

    def test_the_scan_sees_a_planted_forbidden_import(self):
        with tempfile.TemporaryDirectory(prefix="outreach-mcp-scan-") as folder:
            planted = Path(folder) / "planted.py"
            planted.write_text("from outreach.sending import gmail\nimport refresh.refresh\nfrom ...api import app\n",
                               encoding="utf-8")
            self.assertEqual([n for n in imported_names(planted, "outreach.mcp.tools") if forbidden(n)],
                             ["outreach.sending", "outreach.sending.gmail", "refresh.refresh", "outreach.api",
                              "outreach.api.app"])

    def test_mcp_source_names_no_port_loopback_or_route(self):
        for path in sources():
            text = path.read_text(encoding="utf-8")
            self.assertEqual([t for t in FORBIDDEN_TEXT if t in text], [], path)

    def test_mcp_source_never_uses_or_replace_or_or_ignore(self):
        for path in sources():
            text = path.read_text(encoding="utf-8").upper()
            self.assertNotIn("OR REPLACE", text, path)
            self.assertNotIn("OR IGNORE", text, path)

    def test_mcp_source_has_no_em_or_en_dash(self):
        for path in sources():
            text = path.read_text(encoding="utf-8")
            self.assertFalse(any(d in text for d in DASHES), path)

    def test_only_guard_py_opens_a_connection_or_sets_an_authorizer(self):
        for path in sources():
            if path.name == "guard.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("set_authorizer", "connect"):
                    self.fail(f"{path} uses .{node.attr}")
                if isinstance(node, ast.Name) and node.id == "connect":
                    self.fail(f"{path} names connect")
                if isinstance(node, ast.ImportFrom) and any(a.name == "connect" for a in node.names):
                    self.fail(f"{path} imports connect")


if __name__ == "__main__":
    unittest.main()
