"""The outreach API: the one place in `outreach/` that may import third-party packages.

Nothing outside this package imports it (`tests/test_api_boundary.py`), so the sender, the crawler and the MCP
server cannot reach the approve route.
"""
