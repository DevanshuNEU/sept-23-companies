"""The board crawler (#21). The shared contracts are in crawl/base.py.

`run_tick` and `read_company_boards` are loaded on first use, so `import outreach.crawl.stale` (ranking imports it)
loads no adapter, `polite` or `outreach.net` module.
"""
__all__ = ["run_tick", "read_company_boards"]


def __getattr__(name: str):
    if name in __all__:
        from outreach.crawl import tick
        return getattr(tick, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
