"""`GET /api/pulse`: the dashboard's cheap change signal. One number that only grows (the newest event id), so a write
from the MCP bridge shows on an open page within seconds without a websocket or a heavy poll."""
from outreach.mcp.server import run_tool, tool_specs
from tests import apiseed as seed
from tests.apitest import ApiCase, NOW


class PulseTest(ApiCase):
    def pulse(self) -> dict:
        res = self.get("/api/pulse")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_the_pulse_is_one_number_and_nothing_else(self):
        body = self.pulse()
        self.assertEqual(set(body), {"revision"})
        self.assertIsInstance(body["revision"], int)

    def test_it_moves_on_when_anything_writes_an_event_and_never_goes_back(self):
        first = self.pulse()["revision"]
        seed.event(self.conn, None, "x", NOW)
        second = self.pulse()["revision"]
        self.assertGreater(second, first)
        self.assertEqual(self.pulse()["revision"], second)  # a read moves nothing

    def test_an_intake_from_the_terminal_moves_it(self):
        seed.company(self.conn, "Acme Labs", "working")
        before = self.pulse()["revision"]
        spec = next(s for s in tool_specs() if s.name == "intake")
        is_error, payload = run_tool(spec, {"people": [{"name": "Priya Rao", "title": "Software Engineer",
                                                        "company": "Acme Labs"}]},
                                     path=self.settings.db_path, clock=lambda: NOW)
        self.assertFalse(is_error, payload)
        self.assertGreater(self.pulse()["revision"], before)

    def test_a_write_method_on_the_pulse_is_not_a_route(self):
        self.assertEqual(self.post("/api/pulse").status_code, 405)
