"""The win log and its seen marks (#11 Task 13). Ruling 7: recognition fires on any accept or human reply; the log
holds referrals, interviews, offers and Interested replies; a win is never lost until he has seen it."""
import json
import sqlite3

from outreach.outcomes.wins import win_id
from tests import apiseed as s
from tests.apitest import NOW, ApiCase
from tests.test_api_progress import context, no_nan, touch

SENT = "2026-11-03T15:00:00Z"
AT = "2026-11-10T14:41:00Z"  # 09:41 in New York


class WinsCase(ApiCase):
    def setUp(self):
        super().setUp()
        self.acme = s.company(self.conn, "Acme", "working")
        self.dana = s.person(self.conn, self.acme, "Dana Whitfield", channel="linkedin")

    def make(self, person_id: int | None = -1, **cols) -> int:
        person = self.dana if person_id == -1 else person_id
        return touch(self.conn, self.acme, person, sent_at=cols.pop("sent_at", SENT), **cols)

    def wins(self) -> dict:
        res = self.get("/api/wins")
        self.assertEqual(res.status_code, 200, res.text)
        return no_nan(res.text)

    def seen(self, ids, key: str | None = None):
        return self.post("/api/wins/seen", {"winIds": ids}, key=key)

    def seen_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM win_seen ORDER BY touch_id, kind").fetchall()


class WinLog(WinsCase):
    def test_empty_store_wins_view(self):
        self.assertEqual(self.wins(), {"asOf": NOW, "wins": [], "unseen": 0, "since": "2026-11-10",
                                       "counts": {"accepted": 0, "replied": 0, "referred": 0, "interview": 0,
                                                  "offer": 0}})

    def test_wins_log_holds_only_referrals_interviews_offers_and_interested_replies_once_seen(self):
        accepted = self.make(accepted_at=AT)
        not_now = self.make(replied_at=AT, reply_kind="not_now")
        unsorted = self.make(replied_at=AT, reply_kind="unsorted")
        said_no = self.make(replied_at=AT, reply_kind="said_no")
        stop = self.make(replied_at=AT, reply_kind="asked_to_stop")
        interested = self.make(replied_at=AT, reply_kind="interested")
        referred = self.make(referred_at=AT)
        interview = self.make(interview_at=AT)
        offer = self.make(offer_at=AT)
        view = self.wins()
        ids = {w["winId"] for w in view["wins"]}
        everything = {win_id(accepted, "accepted"), win_id(not_now, "replied"), win_id(unsorted, "replied"),
                      win_id(interested, "replied"), win_id(referred, "referred"), win_id(interview, "interview"),
                      win_id(offer, "offer")}
        self.assertEqual(ids, everything)  # unseen: recognition shows accepts and every human reply but no said_no
        self.assertNotIn(win_id(said_no, "replied"), ids)
        self.assertNotIn(win_id(stop, "replied"), ids)
        self.assertEqual(view["unseen"], 7)
        self.seen(sorted(everything))
        after = self.wins()
        self.assertEqual({w["winId"] for w in after["wins"]},
                         {win_id(interested, "replied"), win_id(referred, "referred"),
                          win_id(interview, "interview"), win_id(offer, "offer")})
        self.assertTrue(all(w["unseen"] is False for w in after["wins"]))
        self.assertEqual(after["unseen"], 0)

    def test_unseen_accept_is_in_the_list_until_marked_seen_then_only_in_counts(self):
        t = self.make(accepted_at=AT)
        win = self.wins()["wins"][0]
        self.assertEqual((win["winId"], win["kind"], win["unseen"]), (win_id(t, "accepted"), "accepted", True))
        res = self.seen([win["winId"]])
        self.assertEqual(res.status_code, 200, res.text)
        after = self.wins()
        self.assertEqual(after["wins"], [])
        self.assertEqual(after["counts"]["accepted"], 1)
        self.assertEqual(after["unseen"], 0)

    def test_unseen_survives_two_reads(self):
        self.make(accepted_at=AT)
        self.make(replied_at=AT, reply_kind="interested")
        first, second = self.wins(), self.wins()
        self.assertEqual(first, second)
        self.assertEqual(second["unseen"], 2)
        self.assertEqual(len(second["wins"]), 2)
        self.assertEqual(self.seen_rows(), [])  # reading never marks anything seen

    def test_mark_seen_is_idempotent_and_unknown_id_is_not_found(self):
        t = self.make(accepted_at=AT)
        wid = win_id(t, "accepted")
        first = self.seen([wid], key="seen-key-0001")
        self.assertEqual((first.status_code, first.json()), (200, {"marked": 1}))
        again = self.seen([wid], key="seen-key-0002")  # a new request id: nothing new to mark
        self.assertEqual((again.status_code, again.json()), (200, {"marked": 0}))
        replay = self.seen([wid], key="seen-key-0001")
        self.assertEqual(replay.headers.get("Idempotent-Replay"), "true")
        self.assertEqual(len(self.seen_rows()), 1)
        self.assertEqual(self.seen_rows()[0]["seen_at"], NOW)
        for bad in (win_id(t, "referred"),          # the touch has no referral
                    win_id(t + 50, "accepted"),     # no such touch
                    3, 2 ** 70 + 1, -8):            # not a win id at all
            res = self.seen([bad])
            self.assertEqual(res.status_code, 404, (bad, res.text))
            self.assertEqual(res.json()["code"], "not_found")
        self.assertEqual(len(self.seen_rows()), 1)

    def test_one_unknown_id_marks_none_of_the_batch(self):
        t = self.make(accepted_at=AT)
        res = self.seen([win_id(t, "accepted"), win_id(t, "offer")])
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.seen_rows(), [])
        self.assertEqual(self.wins()["unseen"], 1)

    def test_a_win_on_an_undone_touch_is_not_found(self):
        t = self.make(undone_at=AT)
        self.assertEqual(self.seen([win_id(t, "accepted")]).status_code, 404)

    def test_win_line_has_the_local_time(self):
        self.make(accepted_at=AT)
        self.assertEqual(self.wins()["wins"][0]["line"], "Dana Whitfield accepted your request. 9:41")
        s_ = self.make(interview_at="2026-11-10T19:05:00Z")
        line = next(w["line"] for w in self.wins()["wins"] if w["touchId"] == s_)
        self.assertEqual(line, "Interview at Acme. 14:05")
        self.assertFalse(any(c in line for c in "\u2013\u2014"))

    def test_wins_newest_first(self):
        a = self.make(accepted_at="2026-11-05T15:00:00Z")
        b = self.make(referred_at="2026-11-09T15:00:00Z")
        c = self.make(replied_at="2026-11-07T15:00:00Z", reply_kind="interested")
        self.assertEqual([w["touchId"] for w in self.wins()["wins"]], [b, c, a])

    def test_one_touch_can_hold_several_wins(self):
        t = self.make(accepted_at="2026-11-05T15:00:00Z", replied_at="2026-11-06T15:00:00Z", reply_kind="interested",
                      referred_at="2026-11-08T15:00:00Z")
        view = self.wins()
        self.assertEqual([w["kind"] for w in view["wins"]], ["referred", "replied", "accepted"])
        self.assertEqual({w["touchId"] for w in view["wins"]}, {t})
        self.assertEqual(view["unseen"], 3)

    def test_win_fields(self):
        job = s.posting(self.conn, self.acme, "Software Engineer, New Grad")
        s._insert(self.conn, "linkedin_draft", {
            "person_id": self.dana, "body": "Hi Dana, I saw the new grad role.", "saved_by": "devanshu",
            "reason_state": "confirmed", "reason_line": "New grad role", "created_at": s.T0, "updated_at": s.T0})
        t = self.make(replied_at=AT, reply_kind="interested", posting_id=job)
        win = self.wins()["wins"][0]
        self.assertEqual(win["at"], AT)
        self.assertEqual(win["touchId"], t)
        self.assertEqual(win["person"]["name"], "Dana Whitfield")
        self.assertEqual(win["company"]["name"], "Acme")
        self.assertEqual(win["role"], "Software Engineer, New Grad")
        self.assertEqual(win["sentNote"], "Hi Dana, I saw the new grad role.")
        self.assertEqual(win["replyKind"], "interested")
        self.assertEqual(win["line"], "Dana Whitfield replied and sounds interested. 9:41")

    def test_email_touch_has_no_sent_note_and_an_application_has_no_person(self):
        s._insert(self.conn, "linkedin_draft", {
            "person_id": self.dana, "body": "A LinkedIn note.", "saved_by": "devanshu", "reason_state": "confirmed",
            "reason_line": "x", "created_at": s.T0, "updated_at": s.T0})
        self.make(channel="email", referred_at=AT)
        self.make(None, channel="application", interview_at="2026-11-10T19:05:00Z")
        by_kind = {w["kind"]: w for w in self.wins()["wins"]}
        self.assertIsNone(by_kind["referred"]["sentNote"])
        self.assertIsNone(by_kind["interview"]["person"])
        self.assertIsNone(by_kind["interview"]["sentNote"])
        self.assertIsNone(by_kind["interview"]["role"])
        self.assertIsNone(by_kind["interview"]["replyKind"])

    def test_counts_and_since(self):
        self.make(sent_at="2026-10-12T15:00:00Z", accepted_at="2026-10-13T15:00:00Z")
        self.make(accepted_at=AT, replied_at=AT, reply_kind="not_now")
        self.make(replied_at=AT, reply_kind="said_no")
        self.make(referred_at=AT, interview_at=AT, offer_at=AT)
        self.make(undone_at=AT)
        view = self.wins()
        self.assertEqual(view["counts"], {"accepted": 2, "replied": 1, "referred": 1, "interview": 1, "offer": 1})
        self.assertEqual(view["since"], "2026-10-12")

    def test_a_merged_company_shows_as_its_survivor(self):
        gone = s.company(self.conn, "Acme Old", "removed", merged_into=self.acme)
        person = s.person(self.conn, gone, "Sam", channel="linkedin")
        touch(self.conn, gone, person, sent_at=SENT, accepted_at=AT)
        self.assertEqual(self.wins()["wins"][0]["company"]["id"], self.acme)

    def test_the_json_is_valid_without_nan(self):
        self.make(accepted_at=AT)
        json.loads(self.get("/api/wins").text, parse_constant=lambda t: self.fail(t))

    def test_mark_seen_needs_a_key_and_a_known_body(self):
        res = self.client.post("/api/wins/seen", content=json.dumps({"winIds": []}),
                               headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.post("/api/wins/seen", {"winIds": [], "extra": 1}).status_code, 400)
        self.assertEqual(self.post("/api/wins/seen", {"winIds": []}).json(), {"marked": 0})
