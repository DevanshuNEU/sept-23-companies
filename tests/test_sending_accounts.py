import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from outreach.h1b import lookup
from outreach.sending import accounts
from outreach.sending.config import AccountConfig, SendConfig, load_config
from outreach.store.timefmt import utcnow
from tests.storetest import fresh_store, seed_company, seed_touch

LCA = '{"fy": 2025, "software": 3, "jr": 1, "level1": 1, "newemp_jr": 1}'
NOW = "2026-11-12T15:00:00Z"
GMAIL = "devanshurajesh@gmail.com"
GENERAL = "devanshuchi9@gmail.com"
SPONSOR = "chicholikar.d@northeastern.edu"


def acct(address, lane=None, sends=True, kind="gmail"):
    return AccountConfig(address=address, kind=kind, lane=lane, sends=sends, ramp_start=15, ramp_step=3,
                         ramp_floor=15, ramp_ceiling=100, mailapp_account=None, token_dir=None)


def config(*accts):
    return replace(load_config(), accounts=tuple(accts))


H1B_FILE = """# H-1B approvals by company

Built 2026-11-14 from USCIS's H-1B Employer Data Hub.

| Company | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 | Trend | USCIS name |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Acme | 10 | 12 | 15 | 15 | 15 | steady | ACME INC |
| Loser | 0 | 0 | 0 | 1 | 0 | one year | LOSER INC |
| Alias Co | 5 | 5 | 5 | 5 | 5 | steady | ALIAS CO INC |
"""


def add_fact(conn, company_id, fact, value):
    conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, recorded_at) "
                 "VALUES (?, ?, ?, 'estimate', 'linkedin', '2026-11-01', ?)", (company_id, fact, value, utcnow()))


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"),
                          acct(SPONSOR, "sponsor", sends=False, kind="mailapp"))
        self.h1b_dir = Path(tempfile.mkdtemp(prefix="outreach-h1b-test-"))
        patch = mock.patch.dict(os.environ, {"OUTREACH_H1B_DIR": str(self.h1b_dir)})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lookup.forget)
        lookup.forget()

    def h1b(self, text=H1B_FILE):
        (self.h1b_dir / "companies.md").write_text(text, encoding="utf-8")
        lookup.forget()

    def synced(self, cfg=None):
        return accounts.sync(self.conn, cfg or self.cfg, NOW)

    def company_touch(self, name="Acme"):
        cid = seed_company(self.conn, name)
        return cid, seed_touch(self.conn, cid, f"a@{name.lower()}.com")

    def test_sync_inserts_new_account_once(self):
        first = self.synced()
        self.assertEqual(list(first), [GMAIL, GENERAL, SPONSOR])
        self.assertEqual(first[GMAIL].account_id, 1)
        self.assertEqual(first[SPONSOR].cfg.kind, "mailapp")
        again = self.synced()
        self.assertEqual({k: v.account_id for k, v in first.items()}, {k: v.account_id for k, v in again.items()})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sender_account").fetchone()[0], 3)
        row = self.conn.execute("SELECT kind FROM sender_account WHERE address = ?", (GENERAL,)).fetchone()
        self.assertEqual(row["kind"], "gmail")

    def test_sync_matches_address_case_insensitively(self):
        cfg = config(acct(GMAIL.upper(), sends=False))
        got = self.synced(cfg)
        self.assertEqual(got[GMAIL.upper()].account_id, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sender_account").fetchone()[0], 1)

    def test_h1b_approvals_make_a_sponsor(self):
        cid, _ = self.company_touch("Acme")
        self.h1b()
        self.assertEqual(accounts.lane_of(self.conn, cid), "sponsor")

    def test_everify_enrolled_alone_is_general(self):
        cid, _ = self.company_touch("Other")
        self.h1b()
        add_fact(self.conn, cid, "everify", "enrolled")
        self.assertEqual(accounts.lane_of(self.conn, cid), "general")

    def test_opt_only_alone_is_general(self):
        cid, _ = self.company_touch("Other")
        self.h1b()
        add_fact(self.conn, cid, "sponsorship", "opt_only")
        self.assertEqual(accounts.lane_of(self.conn, cid), "general")

    def test_sponsorship_yes_is_sponsor_even_without_a_list(self):
        cid, _ = self.company_touch("Nolist")
        add_fact(self.conn, cid, "sponsorship", "yes")
        self.assertEqual(accounts.lane_of(self.conn, cid), "sponsor")

    def test_an_lca_row_alone_is_general(self):
        cid, _ = self.company_touch("Alpha")
        add_fact(self.conn, cid, "lca", LCA)
        self.assertEqual(accounts.lane_of(self.conn, cid), "general")

    def test_a_company_not_on_the_list_is_general(self):
        cid, _ = self.company_touch("Beta")
        self.h1b()
        add_fact(self.conn, cid, "sponsorship", "no")
        self.assertEqual(accounts.lane_of(self.conn, cid), "general")

    def test_a_name_alias_finds_the_list_line(self):
        cid, _ = self.company_touch("Aliasless")
        self.h1b()
        self.conn.execute("INSERT INTO company_alias(alias_key, company_id, alias, source, created_at) "
                          "VALUES ('aliasco', ?, 'Alias Co', 'dba', ?)", (cid, NOW))
        self.assertEqual(accounts.lane_of(self.conn, cid), "sponsor")
        _, tid = cid, self.conn.execute("SELECT touch_id FROM touch").fetchone()[0]
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        self.assertEqual(accounts.route(self.conn, cfg, tid, self.synced(cfg))[0].cfg.address, SPONSOR)

    def test_route_uses_the_h1b_lane_and_threads_still_win(self):
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        accts = self.synced(cfg)
        self.h1b()
        _, sponsor_tid = self.company_touch("Acme")
        _, plain_tid = self.company_touch("Plain")
        _, thread_tid = self.company_touch("Alias Co")
        self.conn.execute("UPDATE touch SET account_id = ? WHERE touch_id = ?",
                          (accts[GENERAL].account_id, thread_tid))
        got = accounts.routes(self.conn, cfg, [sponsor_tid, plain_tid, thread_tid], accts)
        self.assertEqual((got[sponsor_tid][0].cfg.address, got[sponsor_tid][1]), (SPONSOR, "lane"))
        self.assertEqual((got[plain_tid][0].cfg.address, got[plain_tid][1]), (GENERAL, "lane"))
        self.assertEqual((got[thread_tid][0].cfg.address, got[thread_tid][1]), (GENERAL, "thread"))

    def test_no_facts_is_general(self):
        cid, _ = self.company_touch()
        self.assertEqual(accounts.lane_of(self.conn, cid), "general")

    def test_route_by_lane(self):
        _, tid = self.company_touch()
        row, reason = accounts.route(self.conn, self.cfg, tid, self.synced())
        self.assertEqual((row.cfg.address, reason), (GENERAL, "lane"))

    def test_followup_uses_touch_account_even_if_lane_changed(self):
        cid, tid = self.company_touch()
        accts = self.synced()
        self.conn.execute("UPDATE touch SET account_id = ? WHERE touch_id = ?", (accts[GENERAL].account_id, tid))
        add_fact(self.conn, cid, "sponsorship", "yes")
        row, reason = accounts.route(self.conn, self.cfg, tid, accts)
        self.assertEqual((row.cfg.address, reason), (GENERAL, "thread"))

    def test_pin_wins_over_lane_when_account_sends(self):
        cid, tid = self.company_touch()
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        accts = self.synced(cfg)
        accounts.pin(self.conn, cid, accts[SPONSOR].account_id, "you", NOW)
        row, reason = accounts.route(self.conn, cfg, tid, accts)
        self.assertEqual((row.cfg.address, reason), (SPONSOR, "pinned"))

    def test_pin_to_resting_account_moves_to_lane(self):
        cid, tid = self.company_touch()
        accts = self.synced()
        accounts.pin(self.conn, cid, accts[GMAIL].account_id, "rule", NOW)
        before = self.conn.execute("SELECT * FROM company_sender").fetchall()
        row, reason = accounts.route(self.conn, self.cfg, tid, accts)
        self.assertEqual((row.cfg.address, reason), (GENERAL, "lane"))
        self.assertEqual([tuple(r) for r in before],
                         [tuple(r) for r in self.conn.execute("SELECT * FROM company_sender").fetchall()])

    def test_sponsor_lane_without_sending_account_waits(self):
        cid, tid = self.company_touch()
        add_fact(self.conn, cid, "sponsorship", "yes")
        row, reason = accounts.route(self.conn, self.cfg, tid, self.synced())
        self.assertEqual((row, reason), (None, "lane_waits"))

    def test_thread_account_not_in_config_is_thread_missing(self):
        _, tid = self.company_touch()
        accts = self.synced()
        self.conn.execute("INSERT INTO sender_account(account_id, address, kind, created_at, updated_at) "
                          "VALUES (9, 'old@example.com', 'gmail', ?, ?)", (NOW, NOW))
        self.conn.execute("UPDATE touch SET account_id = 9 WHERE touch_id = ?", (tid,))
        self.assertEqual(accounts.route(self.conn, self.cfg, tid, accts), (None, "thread_missing"))

    def merged(self):
        loser, tid = self.company_touch("Loser")
        survivor = seed_company(self.conn, "Survivor")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' WHERE company_id = ?",
                          (survivor, loser))
        return loser, survivor, tid

    def test_merged_company_routes_by_survivor_facts(self):
        _, survivor, tid = self.merged()
        add_fact(self.conn, survivor, "sponsorship", "yes")
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        row, reason = accounts.route(self.conn, cfg, tid, self.synced(cfg))
        self.assertEqual((row.cfg.address, reason), (SPONSOR, "lane"))

    def test_a_sponsor_fact_on_the_merged_away_company_counts_for_the_survivor(self):
        # A merge does not move evidence: the loser's H-1B line still makes the survivor a sponsor company.
        loser = seed_company(self.conn, "Loser")
        survivor, tid = self.company_touch("Survivor")
        self.conn.execute("UPDATE company SET merged_into = ?, status = 'removed', remove_reason = 'merged' "
                          "WHERE company_id = ?", (survivor, loser))
        self.h1b()  # the merged-away company's own H-1B line
        self.assertEqual(accounts.lane_of(self.conn, survivor), "sponsor")
        self.assertEqual(accounts.lane_of(self.conn, loser), "sponsor")
        self.assertEqual(accounts.route(self.conn, self.cfg, tid, self.synced()), (None, "lane_waits"))

    def test_pin_on_loser_before_merge_still_counts_and_survivor_pin_wins(self):
        loser, survivor, tid = self.merged()
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        accts = self.synced(cfg)
        self.conn.execute("INSERT INTO company_sender VALUES (?, ?, 'rule', ?)", (loser, accts[SPONSOR].account_id, NOW))
        self.assertEqual(accounts.route(self.conn, cfg, tid, accts)[1], "pinned")
        accounts.pin(self.conn, loser, accts[GENERAL].account_id, "you", NOW)
        row, _ = accounts.route(self.conn, cfg, tid, accts)
        self.assertEqual(row.cfg.address, GENERAL)
        self.assertEqual(self.conn.execute("SELECT account_id FROM company_sender WHERE company_id = ?",
                                           (survivor,)).fetchone()[0], accts[GENERAL].account_id)

    def test_pin_upserts_and_logs_event(self):
        cid, _ = self.company_touch()
        accts = self.synced()
        g, s = accts[GENERAL].account_id, accts[SPONSOR].account_id
        accounts.pin(self.conn, cid, g, "rule", NOW)
        accounts.pin(self.conn, cid, s, "you", NOW)
        pin = self.conn.execute("SELECT * FROM company_sender WHERE company_id = ?", (cid,)).fetchall()
        self.assertEqual([(r["account_id"], r["pinned_by"], r["pinned_at"]) for r in pin], [(s, "you", NOW)])
        events = self.conn.execute("SELECT * FROM event WHERE kind = 'company_pinned' ORDER BY event_id").fetchall()
        self.assertEqual([json.loads(e["detail"]) for e in events], [
            {"account_id": g, "by": "rule", "previous_account_id": None},
            {"account_id": s, "by": "you", "previous_account_id": g}])
        self.assertEqual((events[0]["entity"], events[0]["company_id"], events[0]["actor"], events[1]["actor"]),
                         ("company", cid, "rules", "devanshu"))


if __name__ == "__main__":
    unittest.main()
