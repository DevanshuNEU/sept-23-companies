"""The contract checks every stream proves its parts against (#30 Task 6).

`check_source`, `check_adapter` and `check_signal` each take the running `unittest.TestCase` and assert through it, so
a failure reads as a failure of the stream's own test. A new Source, Adapter or Signal gets one test that calls its
check.
"""
from __future__ import annotations

import unittest

from outreach.crawl.base import Adapter, ReadResult, Validators
from outreach.ranking.types import CompanyFacts, Signal, SignalValue
from outreach.sources.base import Candidate, Source
from tests.fakes import FakeClient

__all__ = ["check_source", "check_adapter", "check_signal"]

_DASHES = (chr(0x2014), chr(0x2013))


def check_source(tc: unittest.TestCase, source: Source, since: str | None = None) -> list[Candidate]:
    """`name` and `version` are non-empty, every item is a `Candidate`, and a second `discover` over the same input
    yields equal candidates (a source is deterministic). Returns the candidates."""
    for attr in ("name", "version"):
        value = getattr(source, attr, None)
        tc.assertIsInstance(value, str, f"source.{attr} must be a string")
        tc.assertTrue(value.strip(), f"source.{attr} must not be empty")
    first = list(source.discover(since))
    for item in first:
        tc.assertIsInstance(item, Candidate, f"discover yielded {type(item).__name__}, not a Candidate")
    second = list(source.discover(since))
    tc.assertEqual(first, second, "a second discover over the same input must yield equal candidates")
    return first


_CONDITIONAL = ("if-none-match", "if-modified-since")


def check_adapter(tc: unittest.TestCase, adapter: Adapter, token: str, *, client: FakeClient | None = None) -> None:
    """Three reads of one board, answered by a `FakeClient` scripted in call order: the first answers a board
    (read 1 is `ok` or `empty`), the second answers 304 or the same ids (read 2, sent with read 1's validators, is
    `unchanged`), the third answers 500 (read 3, sent with read 1's validators, is `error` with no jobs). A read that
    makes several requests scripts each in order.

    An adapter that ignores validators must not pass on a scripted 304, so an `ok` first read must return validators
    (not `Validators()`), and read 2 must show it used them: a request carrying If-None-Match or If-Modified-Since
    when read 1 returned an etag, else (an id-fingerprint ATS with no etag) an `ids_fingerprint` that read 2 compared.
    Afterwards `client.unrouted` is empty: code that swallows an unrouted-request error would otherwise pass by
    accident. The fake is `client`, else `adapter.client`; the check fails when neither is a FakeClient-like object
    with an `unrouted` list and `calls`.
    """
    client = client if client is not None else getattr(adapter, "client", None)
    tc.assertTrue(client is not None and isinstance(getattr(client, "unrouted", None), list)
                  and isinstance(getattr(client, "calls", None), list),
                  "no fake client is reachable: pass client= or expose adapter.client (a tests.fakes.FakeClient)")
    tc.assertTrue(getattr(adapter, "ats", ""), "adapter.ats must not be empty")
    first = adapter.list(token, Validators())
    tc.assertIsInstance(first, ReadResult)
    tc.assertIn(first.result, ("ok", "empty"), f"the first read of a board must be ok or empty, got {first.result}"
                                               f" ({first.error})")
    if first.result == "ok":
        tc.assertNotEqual(first.validators, Validators(),
                          "an ok read must return validators (an etag or an ids_fingerprint) for the next read")
    sent_before = len(client.calls)
    again = adapter.list(token, first.validators)
    second_calls = client.calls[sent_before:]
    tc.assertIsInstance(again, ReadResult)
    tc.assertEqual(again.result, "unchanged",
                   "a read with the validators the first read returned must be unchanged when the board has not "
                   f"changed, got {again.result}")
    tc.assertEqual(again.jobs, (), "an unchanged read carries no jobs")
    if first.validators.etag:
        sent = [{k.lower() for k in headers} for _method, _url, headers, _body in second_calls]
        tc.assertTrue(any(names & set(_CONDITIONAL) for names in sent),
                      "read 1 returned an etag, but read 2 sent no If-None-Match or If-Modified-Since header: "
                      "the adapter ignores the validators it is given")
    elif first.result == "ok":
        tc.assertTrue(first.validators.ids_fingerprint,
                      "with no etag, read 1 must return an ids_fingerprint for read 2 to compare")
    failed = adapter.list(token, first.validators)
    tc.assertIsInstance(failed, ReadResult)
    tc.assertEqual(failed.result, "error", f"an HTTP 500 must be an error read, got {failed.result}")
    tc.assertEqual(failed.jobs, (), "an error read carries no jobs")
    tc.assertTrue(failed.error, "an error read needs a message")
    tc.assertEqual(client.unrouted, [], "the adapter asked for URLs the test did not route (unrouted requests)")


def check_signal(tc: unittest.TestCase, signal: Signal, facts: CompanyFacts) -> SignalValue:
    """The signal has a name and returns a `SignalValue` in -1.0 to 1.0 whose reason is non-empty plain words with no
    em or en dash. Returns the value."""
    tc.assertTrue(getattr(signal, "name", ""), "signal.name must not be empty")
    got = signal.score(facts)
    tc.assertIsInstance(got, SignalValue, f"score returned {type(got).__name__}, not a SignalValue")
    tc.assertIsInstance(got.value, (int, float))
    tc.assertTrue(-1.0 <= got.value <= 1.0, f"signal value {got.value!r} is outside -1.0 to 1.0")
    tc.assertIsInstance(got.reason, str)
    tc.assertTrue(got.reason.strip(), "a signal needs a reason")
    for dash in _DASHES:
        tc.assertNotIn(dash, got.reason, f"the reason {got.reason!r} has an em or en dash")
    return got
