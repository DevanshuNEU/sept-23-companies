"""The nightly first-email drafter (#298): the loaded people are drafted into the Outbox at the pace the accounts can
send, through the MCP tools' own handlers, with a fake model. No test calls a model."""
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from outreach.first_drafter import __main__ as cli
from outreach.first_drafter.config import load_drafter_config
from outreach.first_drafter.pick import Company, Person, how_many, order_in_company, queue, read_pool
from outreach.first_drafter.run import FIXABLE, load_prompt, run_drafter
from outreach.mcp.guard import open_guarded
from outreach.reader.night import RunResult
from tests import apiseed as seed
from tests.mcptest import fingerprint, store_path
from tests.storetest import fresh_store
from tests.test_drafts_checks import FIXTURE_BANNED
from tests.test_mcp_reads import merge, no_go

FIXTURE_MAIL = Path(__file__).parent / "fixtures" / "plan"  # its me.md is the evidence the brief reads
NOW = "2026-11-17T10:00:00Z"  # 05:00 in New York: the job's night
SHIPPED = load_drafter_config()
CFG = replace(SHIPPED, loaded_since=None)  # the seeded people are created on 2026-11-01
SIG = "Devanshu Chicholikar\nhttps://www.devanshuchicholikar.com"  # the fallback signature: tests never read his
# Distinct first emails, so colleagues never read as copies and no sentence repeats across many emails.
BODIES = (
    "Hi {name},\n\nYour team keeps the ledger right overnight, and that is the work I want to do. I built a job "
    "runner in Go with retries and clean shutdowns.\n\nhttps://github.com/devanshu/runner\n\nCould you point me to "
    "whoever owns the backend role?\n\nThanks,\n" + SIG,
    "Hi {name},\n\nThe posting asks for search that answers fast on a cold start. I spent the spring making a code "
    "search tool do that, and I want in on this role.\n\nhttps://github.com/devanshu/search\n\nWho is hiring for "
    "it?\n\nBest,\n" + SIG,
    "Hi {name},\n\nYou wrote the payments retry service. My voice agent checker grades what a call promised against "
    "what it did, and I would like to bring that care to your platform group.\n\nhttps://github.com/devanshu/overhear"
    "\n\nWould a referral be possible?\n\n" + SIG,
    "Hi {name},\n\nThe data team owns the warehouse loads for this job. I wrote a small tool that keeps model spend "
    "inside a budget, simulated on recorded traffic, and I really want this one.\n\nhttps://github.com/devanshu/budget"
    "\n\nIs there someone I should ask about it?\n\nThank you,\n" + SIG,
    "Hi {name},\n\nI saw the infrastructure opening on your board. Last term I shipped a command line tool people use "
    "to summarize long pages, and I would be glad to do that kind of work with you.\n\n"
    "https://github.com/devanshu/saar\n\nWho owns the req?\n\n" + SIG,
    "Hi {name},\n\nYour group runs the mobile release train. I like that problem a lot: my memory service keeps chat "
    "history small and fast on a phone.\n\nhttps://github.com/devanshu/memory\n\nCould we talk for ten minutes about "
    "the role?\n\n" + SIG,
)
THREE_LINKS = ("Hi {name},\n\nLinks: https://a.example https://b.example\n\nWho owns the role?\n\n" + SIG)
SUBJECT = "my reranker made search worse first"


def reply(body: str, *, subject: str = SUBJECT, hook: str = "The posting's words for the work",
          project: str = "OpenCodeIntel", **extra) -> RunResult:
    """What `claude -p --output-format json` prints for a reply of exactly one first email."""
    text = json.dumps({"subject": subject, "body": body, "hook": hook, "project": project, **extra})
    return RunResult(0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text}), "")


class FakeModel:
    """Answers each call with the next of `answers` (a RunResult, or a body template filled with the first name),
    then with `then` (a RunResult) when given, else with a fresh body per person from BODIES. Records every call."""

    def __init__(self, *answers, then: RunResult | None = None):
        self.answers = list(answers)
        self.then = then
        self.calls: list[tuple[str, dict, str]] = []
        self.people: dict[int, int] = {}

    def __call__(self, system_prompt: str, message: str, model: str) -> RunResult:
        sent = json.loads(message)
        self.calls.append((system_prompt, sent, model))
        who = sent["brief"]["person"]["person"]
        answer = self.answers.pop(0) if self.answers else self.then
        if isinstance(answer, RunResult):
            return answer
        if answer is None:
            answer = BODIES[self.people.setdefault(who["id"], len(self.people)) % len(BODIES)]
        return reply(answer.format(name=who["name"].split()[0]))

    def names(self) -> list[str]:
        return [c[1]["brief"]["person"]["person"]["name"] for c in self.calls]


class DrafterCase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(FIXTURE_BANNED),
                                               "OUTREACH_MAIL_HOME": str(FIXTURE_MAIL)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = fresh_store()
        self.addCleanup(self.db.close)
        self.path = store_path(self.db)
        self.acme = seed.company(self.db, "Acme", "working")
        seed.mx(self.db, "acme.example", "ok")

    def person(self, name: str, company=None, *, title="Software Engineer", **kw) -> int:
        return seed.person(self.db, company or self.acme, name, title=title, **kw)

    def first(self, person_id: int):
        return self.db.execute("SELECT m.* FROM message m JOIN touch t ON t.touch_id = m.touch_id "
                               "WHERE t.person_id = ? AND m.seq = 0", (person_id,)).fetchone()

    def waiting(self, n: int, state: str = "drafted") -> None:
        """n first emails already waiting in the Outbox, at a company of their own."""
        other = seed.company(self.db, f"Waiting {state} {n}", "working")
        for i in range(n):
            pid = seed.person(self.db, other, f"Wanda W{i}")
            seed.message(self.db, seed.touch(self.db, pid), 0, state)

    def run_it(self, model, *, cfg=CFG, **kw):
        return run_drafter(model, cfg, now=NOW, path=self.path, **kw)

    def pool(self):
        conn = open_guarded({}, path=self.path)
        try:
            return read_pool(conn)
        finally:
            conn.close()


# ---------------------------------------------------------------------------------------------- pure selection

def _p(pid: int, title: str, company: int = 1) -> Person:
    return Person(person_id=pid, name=f"P{pid}", title=title, company_id=company, company=f"C{company}")


class HowManyTest(unittest.TestCase):
    def test_the_outbox_is_topped_up_to_the_target_and_no_further(self):
        self.assertEqual(how_many(outbox_target=60, waiting=50, max_per_run=30, limit=None), 10)
        self.assertEqual(how_many(outbox_target=60, waiting=0, max_per_run=30, limit=None), 30)
        self.assertEqual(how_many(outbox_target=60, waiting=60, max_per_run=30, limit=None), 0)
        self.assertEqual(how_many(outbox_target=60, waiting=75, max_per_run=30, limit=None), 0)

    def test_limit_lowers_the_run_never_raises_it(self):
        self.assertEqual(how_many(outbox_target=60, waiting=0, max_per_run=30, limit=5), 5)
        self.assertEqual(how_many(outbox_target=60, waiting=0, max_per_run=30, limit=50), 30)
        self.assertEqual(how_many(outbox_target=60, waiting=58, max_per_run=30, limit=5), 2)


class OrderInCompanyTest(unittest.TestCase):
    def titles(self, people, band):
        return [p.title for p in order_in_company(people, band)]

    def test_under_50_founder_then_founding_engineer_then_recruiter_then_engineers(self):
        people = [_p(1, "Software Engineer"), _p(2, "Technical Recruiter"), _p(3, "Account Executive"),
                  _p(4, "Founding Engineer"), _p(5, "CTO")]
        self.assertEqual(self.titles(people, "under_50"), [
            "CTO", "Founding Engineer", "Technical Recruiter", "Software Engineer"])  # #300: no Account Executive

    def test_50_to_200_manager_and_recruiter_first(self):
        people = [_p(1, "Software Engineer"), _p(2, "CEO"), _p(3, "Recruiter"), _p(4, "Engineering Manager")]
        self.assertEqual(self.titles(people, "50_to_200"),
                         ["Engineering Manager", "Recruiter", "CEO", "Software Engineer"])

    def test_unknown_size_takes_the_middle_order(self):
        people = [_p(1, "Software Engineer"), _p(2, "Engineering Manager")]
        self.assertEqual(self.titles(people, "unknown"), ["Engineering Manager", "Software Engineer"])

    def test_over_200_recruiter_first_and_executives_and_rejected_titles_are_skipped(self):
        """#300: a title pick_people would never pick is skipped, not drafted last."""
        people = [_p(1, "CTO"), _p(2, "Software Engineer"), _p(3, "VP of Engineering"), _p(4, "Engineering Manager"),
                  _p(5, "University Recruiter"), _p(6, "Sales Engineer")]
        self.assertEqual(self.titles(people, "over_200"),
                         ["University Recruiter", "Engineering Manager", "Software Engineer"])

    def test_turns_go_round_so_five_are_not_five_recruiters(self):
        people = [_p(i, "Recruiter") for i in range(1, 4)] + [_p(10, "Engineering Manager"), _p(11, "Engineer")]
        self.assertEqual(self.titles(people, "over_200")[:3], ["Recruiter", "Engineering Manager", "Engineer"])

    def test_a_better_title_in_a_group_goes_first(self):
        people = [_p(1, "Recruiter"), _p(2, "University Recruiter")]
        self.assertEqual([p.person_id for p in order_in_company(people, "over_200")], [2, 1])

    def test_titles_pick_people_rejects_are_skipped(self):
        """#300: about 165 loaded people were program, project or product managers, HR or customer success."""
        people = [_p(1, ""), _p(2, "Office Manager"), _p(3, "Intern"), _p(4, "Senior Technical Program Manager"),
                  _p(5, "HR Business Partner (Tech)"), _p(6, "Software Engineer")]
        self.assertEqual([p.person_id for p in order_in_company(people, "under_50")], [6])


class QueueTest(unittest.TestCase):
    def test_companies_take_turns_fewest_sent_first_at_most_per_company(self):
        companies = {1: Company(1, "A", "unknown", sent=3, waiting=0), 2: Company(2, "B", "unknown", sent=0, waiting=0),
                     3: Company(3, "C", "unknown", sent=1, waiting=0)}
        people = [_p(10 * c + i, "Software Engineer", c) for c in (1, 2, 3) for i in range(3)]
        got = queue(people, companies, per_company=2)
        self.assertEqual([p.person_id for p in got], [20, 30, 10, 21, 31, 11])

    def test_a_tie_on_sent_goes_to_the_company_with_fewer_waiting(self):
        companies = {1: Company(1, "A", "unknown", sent=0, waiting=2), 2: Company(2, "B", "unknown", sent=0, waiting=0)}
        got = queue([_p(1, "Engineer", 1), _p(2, "Engineer", 2)], companies, per_company=2)
        self.assertEqual([p.person_id for p in got], [2, 1])

    def test_inside_a_company_the_size_order_holds(self):
        companies = {1: Company(1, "A", "under_50", sent=0, waiting=0)}
        got = queue([_p(1, "Software Engineer"), _p(2, "Founder")], companies, per_company=2)
        self.assertEqual([p.title for p in got], ["Founder", "Software Engineer"])


# ---------------------------------------------------------------------------------------------- reading the store

class PoolTest(DrafterCase):
    def picked(self) -> set[int]:
        return {p.person_id for p in self.pool().people}

    def test_only_people_loaded_on_or_after_loaded_since_are_picked(self):
        """#300: the pool is the loaded list, never older contacts such as a warm interview lead."""
        old = self.person("Olga Older")
        new = self.person("Nina Newer")
        self.db.execute("UPDATE person SET created_at = '2026-11-16T15:00:00Z' WHERE person_id = ?", (new,))
        conn = open_guarded({}, path=self.path)
        try:
            self.assertEqual({p.person_id for p in read_pool(conn, loaded_since="2026-11-16").people}, {new})
            self.assertEqual({p.person_id for p in read_pool(conn).people}, {old, new})
        finally:
            conn.close()
        got = self.run_it(FakeModel(), cfg=replace(CFG, loaded_since="2026-11-16"), dry_run=True)
        self.assertEqual(got.eligible, 1)

    def test_people_with_an_address_and_no_first_email_are_picked(self):
        plain = self.person("Pat Plain")
        touched = self.person("Tom Touched")
        seed.touch(self.db, touched)  # a touch with no first email yet
        self.assertEqual(self.picked(), {plain, touched})

    def test_anyone_with_a_first_email_in_any_state_is_never_picked(self):
        for state in ("drafted", "approved", "sent"):
            pid = self.person(f"Has {state}")
            seed.message(self.db, seed.touch(self.db, pid), 0, state)
        skipped = self.person("Has Skipped")
        mid = seed.message(self.db, seed.touch(self.db, skipped), 0, "drafted")
        self.db.execute("UPDATE message SET state = 'skipped' WHERE message_id = ?", (mid,))
        self.assertEqual(self.picked(), set())

    def test_a_linkedin_contact_or_a_person_with_no_address_is_never_picked(self):
        self.person("Lin Kedin", channel="linkedin")
        imported = self.person("No Address")  # an import may hold an email person with no address yet
        self.db.execute("UPDATE person SET added_by = 'import', email = NULL WHERE person_id = ?", (imported,))
        self.assertEqual(self.picked(), set())

    def test_closed_removed_and_no_go_companies_and_no_go_people_are_never_picked(self):
        for status in ("closed", "removed"):
            self.person(f"At {status}", seed.company(self.db, f"Co {status}", status))
        nogo = seed.company(self.db, "NoGo Co", "working")
        self.person("At NoGo", nogo)
        no_go(self.db, company_id=nogo)
        no_go(self.db, person_id=self.person("Person NoGo"))
        lifted = self.person("Lifted NoGo")
        no_go(self.db, person_id=lifted, lifted=True)
        fine = self.person("Fine Person")
        self.assertEqual(self.picked(), {fine, lifted})

    def test_a_merged_duplicate_counts_as_its_survivor(self):
        closed = seed.company(self.db, "Closed Survivor", "closed")
        dup = seed.company(self.db, "Dup of closed", "working")
        self.person("At Dup Closed", dup)
        merge(self.db, dup, closed)
        dup_ok = seed.company(self.db, "Dup of Acme", "working")
        at_dup = self.person("At Dup Acme", dup_ok)
        merge(self.db, dup_ok, self.acme)
        pool = self.pool()
        self.assertEqual([(p.person_id, p.company_id) for p in pool.people], [(at_dup, self.acme)])
        self.assertIn(self.acme, pool.companies)

    def test_a_held_company_and_a_person_who_replied_are_never_picked(self):
        held_co = seed.company(self.db, "Held Co", "working")
        replier = self.person("Rae Replied", held_co)
        tid = seed.touch(self.db, replier, "replied")
        seed.send_first(self.db, tid)
        self.db.execute("UPDATE company SET held_by_touch = ?, held_at = ? WHERE company_id = ?",
                        (tid, seed.T0, held_co))
        self.person("Colleague Held", held_co)
        self.assertEqual(self.picked(), set())

    def test_a_person_who_replied_on_linkedin_is_never_picked_after_the_hold_is_released(self):
        from tests.test_api_shell import outreach_touch
        talker = self.person("Tal Talked")
        outreach_touch(self.db, self.acme, talker, replied_at="2026-11-10T14:00:00Z", reply_kind="interested")
        self.db.execute("INSERT INTO event(at, recorded_at, entity, entity_id, company_id, kind, actor, detail) "
                        "VALUES (?, ?, 'company', ?, ?, 'hold_released', 'devanshu', '{}')",
                        ("2026-11-11T10:00:00Z", "2026-11-11T10:00:00Z", self.acme, self.acme))
        colleague = self.person("Col League")
        self.assertEqual(self.picked(), {colleague})

    def test_waiting_counts_drafted_and_approved_first_emails_only(self):
        self.waiting(2, "drafted")
        self.waiting(1, "approved")
        self.waiting(4, "sent")
        self.assertEqual(self.pool().waiting, 3)

    def test_sent_and_waiting_are_counted_per_company(self):
        sent = self.person("Sent One")
        seed.send_first(self.db, seed.touch(self.db, sent))
        drafted = self.person("Drafted One")
        seed.message(self.db, seed.touch(self.db, drafted), 0, "drafted")
        self.person("Fresh One")
        acme = self.pool().companies[self.acme]
        self.assertEqual((acme.sent, acme.waiting), (1, 1))

    def test_the_size_band_comes_from_knocks_size(self):
        small = seed.company(self.db, "Small Co", "working")
        seed.evidence(self.db, small, "size", 30)
        self.person("At Small", small)
        self.person("At Acme")
        pool = self.pool()
        self.assertEqual((pool.companies[small].band, pool.companies[self.acme].band), ("under_50", "unknown"))


# ---------------------------------------------------------------------------------------------- the run

class DraftTest(DrafterCase):
    def test_each_picked_person_gets_a_first_email_drafted_by_claude_never_approved(self):
        pat = self.person("Pat Lee")
        beta = seed.company(self.db, "Beta", "working")
        bo = self.person("Bo Chen", beta)
        summary = self.run_it(FakeModel())
        self.assertEqual((summary.status, summary.drafted, summary.calls), ("done", 2, 2))
        for pid in (pat, bo):
            row = self.first(pid)
            self.assertEqual((row["state"], row["saved_by"]), ("drafted", "claude"))
            self.assertIsNone(row["approved_at"])
            self.assertEqual((row["subject"], row["hook"], row["project"]),
                             (SUBJECT, "The posting's words for the work", "OpenCodeIntel"))
            self.assertTrue(row["body"].startswith("Hi "))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message WHERE state <> 'drafted'").fetchone()[0], 0)

    def test_the_model_gets_the_pinned_prompt_and_both_parts_of_the_brief(self):
        self.person("Pat Lee")
        model = FakeModel()
        self.run_it(model)
        system, sent, name = model.calls[0]
        self.assertEqual(system, load_prompt())
        self.assertEqual(name, CFG.model)
        brief = sent["brief"]
        for key in ("voice", "stories", "evidence", "posting", "company_facts", "avoid", "signature", "limits",
                    "person"):
            self.assertIn(key, brief)
        for key in ("brief_id", "part", "parts", "next", "pack_id", "can_draft_email"):
            self.assertNotIn(key, brief)
        self.assertEqual(brief["limits"]["words"], [0, 120])
        self.assertEqual(brief["limits"]["max_links"], 2)
        self.assertNotIn("previous", sent)

    def test_the_run_tops_the_outbox_up_to_the_target(self):
        self.waiting(3)
        for i in range(4):
            self.person(f"Pat P{i}", seed.company(self.db, f"Co {i}", "working"))
        summary = self.run_it(FakeModel(), cfg=replace(CFG, outbox_target=5))
        self.assertEqual((summary.waiting, summary.wanted, summary.drafted), (3, 2, 2))

    def test_a_full_outbox_drafts_nobody(self):
        self.waiting(2)
        self.person("Pat Lee")
        model = FakeModel()
        summary = self.run_it(model, cfg=replace(CFG, outbox_target=2))
        self.assertEqual((summary.status, model.calls), ("nothing_to_do", []))

    def test_nobody_left_to_draft_is_nothing_to_do(self):
        summary = self.run_it(FakeModel())
        self.assertEqual((summary.status, summary.calls), ("nothing_to_do", 0))

    def test_max_per_run_and_limit_cap_the_run(self):
        for i in range(4):
            self.person(f"Pat P{i}", seed.company(self.db, f"Co {i}", "working"))
        self.assertEqual(self.run_it(FakeModel(), cfg=replace(CFG, max_per_run=3)).drafted, 3)
        self.assertEqual(self.run_it(FakeModel(), limit=1).drafted, 1)

    def test_at_most_per_company_at_one_company_a_night(self):
        for i in range(4):
            self.person(f"Pat P{i}")
        summary = self.run_it(FakeModel(), cfg=replace(CFG, per_company=2))
        self.assertEqual(summary.drafted, 2)
        drafted = self.db.execute("SELECT COUNT(*) FROM message WHERE seq = 0").fetchone()[0]
        self.assertEqual(drafted, 2)

    def test_companies_take_turns_and_the_size_order_holds_inside_one(self):
        small = seed.company(self.db, "Small Co", "working")
        seed.evidence(self.db, small, "size", 20)
        self.person("Eng Small", small, title="Software Engineer")
        self.person("Fay Founder", small, title="Founder and CEO")
        busy = seed.company(self.db, "Busy Co", "working")
        sent = self.person("Old Sent", busy)
        seed.send_first(self.db, seed.touch(self.db, sent))
        self.person("Busy Eng", busy)
        model = FakeModel()
        self.run_it(model, cfg=replace(CFG, per_company=2))
        self.assertEqual(model.names(), ["Fay Founder", "Busy Eng", "Eng Small"])  # Small Co has none sent

    def test_a_rerun_never_drafts_the_same_person_twice(self):
        pat = self.person("Pat Lee")
        self.run_it(FakeModel())
        model = FakeModel()
        summary = self.run_it(model)
        self.assertEqual((summary.status, model.calls), ("nothing_to_do", []))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message WHERE seq = 0").fetchone()[0], 1)
        self.assertEqual(self.first(pat)["revision"], 1)


class BlocksTest(DrafterCase):
    def test_the_fixable_codes_cover_the_text_blocks_and_the_first_email_rules(self):
        for code in ("near_identical", "markdown_link", "too_many_links", "visa_line", "placeholder",
                     "voice_ungrounded", "link_shortener", "link_count", "length", "signature"):
            self.assertIn(code, FIXABLE)
        for code in ("no_mail_server", "banned_list_missing", "no_go_person", "said_no", "closed_no_sponsorship",
                     "no_person_fact", "mail_server_unknown", "followup_subject"):
            self.assertNotIn(code, FIXABLE)

    def test_a_draft_with_a_fixable_block_is_retried_once_and_the_better_one_kept(self):
        pat = self.person("Pat Lee")
        bad = "Hi {name},\n\nhttps://a.example\nhttps://b.example\n\nWho owns the role?\n\n" + SIG
        model = FakeModel(bad, BODIES[0])
        summary = self.run_it(model)
        self.assertEqual((summary.drafted, summary.with_blocks, summary.calls), (1, 0, 2))
        retry = model.calls[1][1]
        self.assertEqual(retry["previous"]["body"], bad.format(name="Pat"))
        self.assertEqual(retry["previous"]["subject"], SUBJECT)
        self.assertIn("More than 2 links", [b["title"] for b in retry["blocks"]])
        self.assertEqual(self.first(pat)["body"], BODIES[0].format(name="Pat"))

    def test_a_visa_line_is_retried(self):
        pat = self.person("Pat Lee")
        visa = BODIES[1].replace("and I want in on this role.", "and I am on OPT.")
        summary = self.run_it(FakeModel(visa, BODIES[1]))
        self.assertEqual(summary.calls, 2)
        self.assertEqual(self.first(pat)["body"], BODIES[1].format(name="Pat"))

    def test_a_copy_of_a_colleagues_draft_is_retried(self):
        self.person("Pat Lee")
        sam = self.person("Sam Lee")
        model = FakeModel(BODIES[0], BODIES[0], BODIES[2])
        summary = self.run_it(model, cfg=replace(CFG, per_company=2))
        self.assertEqual((summary.drafted, summary.calls), (2, 3))
        self.assertIn("Near-identical to a colleague's draft", [b["title"] for b in model.calls[2][1]["blocks"]])
        self.assertEqual(self.first(sam)["body"], BODIES[2].format(name="Sam"))

    def test_a_worse_retry_keeps_the_first_draft(self):
        pat = self.person("Pat Lee")
        worse = THREE_LINKS.replace("Who owns", "{{{{company}}}} who owns")  # the same 3 links and a placeholder
        summary = self.run_it(FakeModel(THREE_LINKS, worse))
        self.assertEqual(summary.calls, 2)
        self.assertEqual(self.first(pat)["body"], THREE_LINKS.format(name="Pat"))
        self.assertEqual(self.first(pat)["state"], "drafted")

    def test_a_retry_with_a_block_loses_to_a_first_draft_with_only_warnings(self):
        pat = self.person("Pat Lee")
        warned = ("Hi {name},\n\nYour team keeps the ledger right " + chr(0x2014) + " I want in.\n\n"
                  "https://github.com/devanshu/runner\n\nWho owns the role?")  # a dash, 1 link, no signature
        blocked = BODIES[0].replace("https://github.com/devanshu/runner", "[runner](https://github.com/devanshu/runner)")
        model = FakeModel(warned, blocked)
        summary = self.run_it(model)
        self.assertEqual((summary.calls, summary.with_blocks), (2, 0))
        self.assertGreaterEqual(len(model.calls[1][1]["blocks"]), 3)
        self.assertEqual(self.first(pat)["body"], warned.format(name="Pat"))

    def test_blocks_left_after_the_retry_still_save_for_him_to_see(self):
        pat = self.person("Pat Lee")
        three = "Hi {name},\n\nhttps://a.example https://b.example https://c.example\n\n" + SIG
        summary = self.run_it(FakeModel(three, three))
        self.assertEqual((summary.drafted, summary.with_blocks, summary.calls), (1, 1, 2))
        self.assertEqual(self.first(pat)["state"], "drafted")

    def test_a_block_the_model_cannot_fix_gets_no_retry(self):
        pat = self.person("Pat Lee")
        with mock.patch.dict(os.environ, {"OUTREACH_BANNED_PHRASES": str(Path(self.path).parent / "missing.txt")}):
            summary = self.run_it(FakeModel())
        self.assertEqual((summary.calls, summary.drafted, summary.with_blocks), (1, 1, 1))
        self.assertEqual(self.first(pat)["state"], "drafted")

    def test_a_session_limit_on_the_retry_keeps_the_first_draft_counted(self):
        pat = self.person("Pat Lee")
        summary = self.run_it(FakeModel(THREE_LINKS, RunResult(1, "", "usage limit reached")))
        self.assertEqual((summary.status, summary.drafted, summary.with_blocks), ("session_limit", 1, 1))
        self.assertEqual(self.first(pat)["state"], "drafted")


class FailureTest(DrafterCase):
    def two(self) -> tuple[int, int]:
        return self.person("Pat Lee"), self.person("Bo Chen", seed.company(self.db, "Beta", "working"))

    def test_a_session_limit_stops_the_run_and_writes_nothing_more(self):
        self.two()
        summary = self.run_it(FakeModel(then=RunResult(1, "", "Claude usage limit reached. Resets at 5am.")))
        self.assertEqual((summary.status, summary.calls, summary.drafted), ("session_limit", 1, 0))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message").fetchone()[0], 0)

    def test_a_login_problem_stops_the_run(self):
        self.two()
        summary = self.run_it(FakeModel(then=RunResult(1, "", "Not logged in. Please run /login")))
        self.assertEqual((summary.status, summary.calls), ("auth", 1))

    def test_a_broken_cli_stops_the_run(self):
        self.two()

        def missing(*_args):
            raise FileNotFoundError("claude")

        summary = self.run_it(missing)
        self.assertEqual(summary.status, "failed")
        self.assertIn("claude CLI", summary.reason)

    def test_failed_calls_in_a_row_stop_the_run(self):
        for i in range(3):
            self.person(f"Pat P{i}", seed.company(self.db, f"Co {i}", "working"))
        summary = self.run_it(FakeModel(then=RunResult(1, "", "boom")), cfg=replace(CFG, max_consecutive_failures=2))
        self.assertEqual((summary.status, summary.calls, summary.failed), ("failed", 2, 2))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message").fetchone()[0], 0)

    def bad_reply_is_counted_and_the_run_goes_on(self, bad: RunResult):
        pat, bo = self.two()
        summary = self.run_it(FakeModel(bad))
        self.assertEqual((summary.status, summary.failed, summary.drafted), ("done", 1, 1))
        self.assertEqual(sum(1 for pid in (pat, bo) if self.first(pid) is None), 1)

    def test_a_reply_in_prose_is_a_failure_and_the_run_goes_on(self):
        prose = {"type": "result", "is_error": False, "result": "Here is a draft: Hi Pat"}
        self.bad_reply_is_counted_and_the_run_goes_on(RunResult(0, json.dumps(prose), ""))

    def test_a_reply_missing_the_hook_is_a_failure(self):
        text = json.dumps({"subject": "s", "body": "Hi Pat", "project": "x"})
        self.bad_reply_is_counted_and_the_run_goes_on(RunResult(0, json.dumps({"type": "result", "is_error": False,
                                                                                "result": text}), ""))

    def test_a_reply_with_an_extra_key_is_a_failure(self):
        self.bad_reply_is_counted_and_the_run_goes_on(reply("Hi Pat", options=["a", "b"]))

    def test_a_reply_with_an_empty_subject_is_a_failure(self):
        self.bad_reply_is_counted_and_the_run_goes_on(reply("Hi Pat", subject="  "))

    def test_a_refused_save_is_counted_and_the_run_goes_on(self):
        # Acme closed between the pick and the save: save_draft refuses, nothing is written for Pat.
        pat, bo = self.two()
        model = FakeModel()

        def closing(system_prompt, message, name):
            if not model.calls:
                self.db.execute("UPDATE company SET status = 'closed', close_reason = 'other' WHERE company_id = ?",
                                (self.acme,))
            return model(system_prompt, message, name)

        summary = self.run_it(closing)
        self.assertEqual((summary.refused, summary.drafted, summary.status), (1, 1, "done"))
        self.assertIsNone(self.first(pat))
        self.assertEqual(self.first(bo)["state"], "drafted")
        self.assertTrue(any("company_closed" in line for line in summary.lines))

    def test_a_refused_brief_is_counted_and_the_run_goes_on(self):
        pat, bo = self.two()
        from outreach.first_drafter import run as drafter_run
        real = drafter_run.run_tool

        def refusing(spec, arguments, **kw):
            if spec.name == "draft_brief" and arguments["person_id"] == pat:
                return True, {"code": "not_found", "message": "No person.", "fix": "Use another id."}
            return real(spec, arguments, **kw)

        model = FakeModel()
        with mock.patch.object(drafter_run, "run_tool", refusing):
            summary = self.run_it(model)
        self.assertEqual((summary.refused, summary.drafted, summary.calls), (1, 1, 1))
        self.assertEqual(model.names(), ["Bo Chen"])

    def test_a_brief_that_says_no_email_is_skipped_and_counted(self):
        pat, bo = self.two()
        from outreach.first_drafter import run as drafter_run
        real = drafter_run.run_tool

        def cannot(spec, arguments, **kw):
            is_error, got = real(spec, arguments, **kw)
            if spec.name == "draft_brief" and arguments["person_id"] == pat and arguments["part"] == 1:
                got = {**got, "can_draft_email": False, "person": {**got["person"], "why_not": "said_no"}}
            return is_error, got

        model = FakeModel()
        with mock.patch.object(drafter_run, "run_tool", cannot):
            summary = self.run_it(model)
        self.assertEqual((summary.refused, summary.drafted), (1, 1))
        self.assertIsNone(self.first(pat))
        self.assertTrue(any("said_no" in line for line in summary.lines))

    def test_unreadable_voice_files_stop_the_run_before_any_call(self):
        self.two()
        model = FakeModel()
        with mock.patch.dict(os.environ, {"OUTREACH_VOICE_DIR": str(Path(self.path).parent / "no-voice")}):
            summary = self.run_it(model)
        self.assertEqual((summary.status, model.calls), ("failed", []))
        self.assertIn("voice", summary.reason)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM message").fetchone()[0], 0)


class OffAndDryRunTest(DrafterCase):
    def test_off_calls_nothing_and_writes_nothing(self):
        self.person("Pat Lee")
        before = fingerprint(self.db)
        model = FakeModel()
        summary = self.run_it(model, cfg=replace(CFG, enabled=False))
        self.assertEqual((summary.status, model.calls), ("off", []))
        self.assertEqual(fingerprint(self.db), before)

    def test_a_dry_run_lists_who_would_be_drafted_and_calls_and_writes_nothing(self):
        self.person("Pat Lee")
        self.person("Sam Lee")
        self.person("Uma Lee")
        before = fingerprint(self.db)
        model = FakeModel()
        summary = self.run_it(model, dry_run=True, cfg=replace(CFG, per_company=2))
        self.assertEqual((summary.status, summary.wanted, model.calls), ("dry_run", 2, []))
        self.assertEqual(len(summary.lines), 2)
        self.assertTrue(all("at Acme" in line for line in summary.lines))
        self.assertEqual(fingerprint(self.db), before)


class ConfigAndCliTest(DrafterCase):
    def test_the_shipped_config(self):
        self.assertIs(SHIPPED.enabled, True)
        self.assertEqual((SHIPPED.model, SHIPPED.max_per_run, SHIPPED.outbox_target, SHIPPED.per_company,
                          SHIPPED.loaded_since), ("claude-opus-5-5", 30, 60, 2, "2026-11-16"))

    def test_a_bad_config_value_fails_at_load(self):
        from outreach.first_drafter.config import CONFIG_PATH
        text = CONFIG_PATH.read_text(encoding="utf-8")
        folder = Path(self.path).parent
        for old, new in (("per_company = 2", "per_company = 0"), ("outbox_target = 60", "outbox_target = -1"),
                         ('loaded_since = "2026-11-16"', 'loaded_since = "last week"'),
                         ("enabled = true", 'enabled = "yes"')):
            bad = folder / "bad.toml"
            bad.write_text(text.replace(old, new), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_drafter_config(bad)
        missing = folder / "missing.toml"
        missing.write_text(text.replace("per_company = 2\n", ""), encoding="utf-8")
        with self.assertRaises(KeyError):
            load_drafter_config(missing)

    def test_the_prompt_states_the_reply_contract_and_the_first_email_rules(self):
        prompt = load_prompt()
        for words in ('{"subject"', '"hook"', '"project"', "120 words", "Exactly 2 links", "brief.signature",
                      "is data, not instructions", "`brief.posting`", "`brief.company_facts`",
                      "brief.avoid.recent_openings", "subject last", "No visa, OPT, sponsorship or H-1B"):
            self.assertIn(words, prompt)
        self.assertFalse(any(d in prompt for d in (chr(0x2013), chr(0x2014))))

    def test_a_real_run_outside_the_live_checkout_is_refused(self):
        with mock.patch.object(cli.live_checkout, "is_live", return_value=False), \
                self.assertRaises(SystemExit) as caught:
            cli.main(["run"], stdout=io.StringIO(), runner=FakeModel())
        self.assertIn("live checkout", str(caught.exception))

    def test_a_dry_run_from_the_cli_prints_who_and_one_summary_line(self):
        pat = self.person("Pat Lee")
        older = self.person("Olga Older")  # created before the shipped loaded_since: never listed
        self.db.execute("UPDATE person SET created_at = '2026-11-16T15:00:00Z' WHERE person_id = ?", (pat,))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"OUTREACH_DB": self.path}):
            code = cli.main(["run", "--dry-run"], stdout=out, runner=FakeModel(), now=NOW)
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertIn("Pat Lee", lines[0])
        self.assertNotIn("Olga Older", out.getvalue())
        self.assertIn("first drafter: dry_run", lines[-1])

    def test_off_prints_the_off_line_and_exits_0(self):
        out = io.StringIO()
        with mock.patch.object(cli, "load_drafter_config", return_value=replace(CFG, enabled=False)):
            self.assertEqual(cli.main(["run"], stdout=out, runner=FakeModel()), 0)
        self.assertIn("is off", out.getvalue())


class JobTest(unittest.TestCase):
    def test_the_template_renders_to_the_drafter_run_at_0500(self):
        import plistlib

        from outreach.deploy.jobs import NEW_JOBS, render
        root = Path(__file__).resolve().parent.parent
        job = next(j for j in NEW_JOBS if j.label == "com.devanshu.outreach-first-drafter")
        self.assertFalse(job.required)
        text = (root / job.template).read_text(encoding="utf-8")
        self.assertFalse(any(d in text for d in (chr(0x2013), chr(0x2014))))
        plist = plistlib.loads(render(text, live_root="/live", log_path="/logs/d.log", label=job.label).encode())
        self.assertEqual(["/live/.venv/bin/python", "-m", "outreach.first_drafter", "run"], plist["ProgramArguments"])
        self.assertEqual({"Hour": 5, "Minute": 0}, plist["StartCalendarInterval"])
        self.assertEqual(("/logs/d.log",) * 2, (plist["StandardOutPath"], plist["StandardErrorPath"]))
        self.assertEqual("/live", plist["WorkingDirectory"])


if __name__ == "__main__":
    unittest.main()
