import json
import unittest
from dataclasses import replace

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


def add_fact(conn, company_id, fact, value):
    conn.execute("INSERT INTO evidence(company_id, fact, value, confidence, source_kind, observed_on, recorded_at) "
                 "VALUES (?, ?, ?, 'estimate', 'linkedin', '2026-11-01', ?)", (company_id, fact, value, utcnow()))


class AccountsTest(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_store()
        self.addCleanup(self.conn.close)
        self.cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"),
                          acct(SPONSOR, "sponsor", sends=False, kind="mailapp"))

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

    def test_everify_enrolled_is_sponsor(self):
        cid, _ = self.company_touch()
        add_fact(self.conn, cid, "everify", "enrolled")
        self.assertEqual(accounts.lane_of(self.conn, cid), "sponsor")

    def test_opt_only_is_sponsor(self):
        cid, _ = self.company_touch()
        add_fact(self.conn, cid, "sponsorship", "opt_only")
        self.assertEqual(accounts.lane_of(self.conn, cid), "sponsor")

    def test_lca_row_is_sponsor_and_other_values_are_not(self):
        a, _ = self.company_touch("Alpha")
        b, _ = self.company_touch("Beta")
        add_fact(self.conn, a, "lca", LCA)
        add_fact(self.conn, b, "sponsorship", "no")
        add_fact(self.conn, b, "everify", "not_enrolled")
        self.assertEqual(accounts.lane_of(self.conn, a), "sponsor")
        self.assertEqual(accounts.lane_of(self.conn, b), "general")

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
        add_fact(self.conn, cid, "everify", "enrolled")
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
        add_fact(self.conn, cid, "everify", "enrolled")
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
        add_fact(self.conn, survivor, "everify", "enrolled")
        cfg = config(acct(GMAIL, sends=False), acct(GENERAL, "general"), acct(SPONSOR, "sponsor"))
        row, reason = accounts.route(self.conn, cfg, tid, self.synced(cfg))
        self.assertEqual((row.cfg.address, reason), (SPONSOR, "lane"))

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
