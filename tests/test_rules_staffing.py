"""Client wording in a posting's opening (#257): a recruiter's posting for an unnamed client."""
import unittest

from outreach.rules import PostingInput, decide
from outreach.rules.config import default_config
from outreach.rules.staffing import for_client

RAYDAR = ("Our client is a 15-person consumer AI company building an agentic travel platform that understands "
          "individual preferences and can plan and book real-time travel experiences.")


class ForClientTest(unittest.TestCase):
    def setUp(self):
        self.cfg = default_config()

    def test_client_wording_in_the_opening(self):
        for body in (RAYDAR, "My client, a Series A fintech, is hiring a backend engineer.",
                     "We are recruiting on behalf of our client, a robotics startup.",
                     "ABOUT THE ROLE\n\nOur client, a seed-stage AI lab, needs a founding engineer."):
            with self.subTest(body=body[:30]):
                self.assertTrue(for_client(body, self.cfg))

    def test_our_clients_is_not_a_recruiter(self):
        for body in ("We help our clients modernize their banking platforms.",
                     "Our clients rely on us for engineering, design and consulting.", ""):
            with self.subTest(body=body[:30]):
                self.assertFalse(for_client(body, self.cfg))

    def test_only_the_opening_counts(self):
        body = "x" * 400 + " Our client is a startup."
        self.assertFalse(for_client(body, self.cfg))

    def test_decide_stores_it(self):
        self.assertTrue(decide(PostingInput(title="Software Engineer", body=RAYDAR)).for_client)
        self.assertFalse(decide(PostingInput(title="Software Engineer", body="We build tools.")).for_client)
        self.assertEqual(decide(PostingInput(title="Software Engineer", body=RAYDAR)).as_row()["for_client"], 1)


if __name__ == "__main__":
    unittest.main()
