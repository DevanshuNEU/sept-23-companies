import logging
import unittest

from outreach.api.errors import STATUS, ApiProblem, ErrorEnvelope
from outreach.api.models.base import Wire
from tests.apitest import ApiCase

PROBES = '''
from fastapi import APIRouter
from outreach.api.errors import ApiProblem
from outreach.api.models.base import Wire, WireRequest

router = APIRouter()


class Person(Wire):
    person_id: int
    full_name: str = "none"


@router.get("/probe/problem/{code}")
def problem(code: str):
    raise ApiProblem(code, f"the {code} message")


@router.get("/probe/problem-with-checks")
def with_checks():
    raise ApiProblem("blocked", "Blocked.", checks=[{
        "code": "no_go", "severity": "block", "title": "No-go", "detail": "d",
        "fix": {"kind": "no_go", "noGoId": 4}, "spans": [{"field": "body", "start": 1, "end": 3}],
    }])


@router.post("/probe/echo", response_model=Person)
def echo(body: Person) -> Person:
    return body


class Strict(WireRequest):
    first_name: str


@router.post("/probe/strict")
def strict(body: Strict):
    return {"ok": True}


@router.get("/probe/camel", response_model=Person)
def camel() -> Person:
    return Person(person_id=7, full_name="Ada")


@router.post("/probe/boom")
def boom(body: dict):
    raise RuntimeError("internal detail")


@router.get("/probe/query")
def query(limit: int):
    return {"limit": limit}
'''


class PathIdTest(ApiCase):
    def test_every_id_in_a_path_is_bounded_to_a_row_id(self):
        from outreach.api.routers import MAX_ID
        checked = []
        for path, methods in self.app.openapi()["paths"].items():
            for method, op in methods.items():
                for param in op.get("parameters", []):
                    if param["in"] != "path" or not param["name"].endswith("_id"):
                        continue
                    with self.subTest(path=path, method=method, param=param["name"]):
                        schema = param["schema"]
                        self.assertEqual((schema.get("minimum"), schema.get("maximum")), (1, MAX_ID))
                    checked.append((path, method))
        self.assertGreaterEqual(len(checked), 10)

    def test_a_huge_id_is_a_bad_request_not_a_500(self):
        huge = 2 ** 63
        for path in (f"/api/companies/{huge}", f"/api/companies/{huge}/score", f"/api/touches/{huge}/history"):
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertEqual((reply.status_code, reply.json()["code"]), (400, "bad_request"), reply.text)
        reply = self.post(f"/api/watch/{huge}/dismiss", {})
        self.assertEqual((reply.status_code, reply.json()["code"]), (400, "bad_request"), reply.text)
        self.assertIn("item_id", reply.json()["message"])


class EnvelopeTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.use_routers(zz_probes=PROBES)

    def assert_envelope(self, reply, status, code):
        self.assertEqual(reply.status_code, status, reply.text)
        body = reply.json()
        self.assertEqual(set(body), {"code", "message", "checks"})
        self.assertEqual(body["code"], code)
        self.assertIsInstance(body["message"], str)
        self.assertIsInstance(body["checks"], list)
        return body

    def test_the_table_of_codes_and_statuses(self):
        self.assertEqual(STATUS, {
            "blocked": 422, "conflict": 409, "capacity": 409, "not_allowed": 409, "not_found": 404,
            "forbidden": 403, "bad_request": 400, "idempotency_mismatch": 422, "busy": 503,
        })

    def test_blocked_is_422(self):
        self.assert_envelope(self.get("/api/probe/problem/blocked"), 422, "blocked")

    def test_conflict_is_409(self):
        self.assert_envelope(self.get("/api/probe/problem/conflict"), 409, "conflict")

    def test_capacity_is_409(self):
        self.assert_envelope(self.get("/api/probe/problem/capacity"), 409, "capacity")

    def test_not_allowed_is_409(self):
        self.assert_envelope(self.get("/api/probe/problem/not_allowed"), 409, "not_allowed")

    def test_not_found_is_404(self):
        self.assert_envelope(self.get("/api/probe/problem/not_found"), 404, "not_found")

    def test_forbidden_is_403(self):
        self.assert_envelope(self.get("/api/probe/problem/forbidden"), 403, "forbidden")

    def test_bad_request_is_400(self):
        self.assert_envelope(self.get("/api/probe/problem/bad_request"), 400, "bad_request")

    def test_idempotency_mismatch_is_422(self):
        self.assert_envelope(self.get("/api/probe/problem/idempotency_mismatch"), 422, "idempotency_mismatch")

    def test_busy_is_503(self):
        self.assert_envelope(self.get("/api/probe/problem/busy"), 503, "busy")

    def test_the_message_is_the_one_raised(self):
        self.assertEqual(self.get("/api/probe/problem/conflict").json()["message"], "the conflict message")

    def test_checks_travel_in_the_envelope_in_camel_case(self):
        body = self.assert_envelope(self.get("/api/probe/problem-with-checks"), 422, "blocked")
        self.assertEqual(body["checks"], [{
            "code": "no_go", "severity": "block", "title": "No-go", "detail": "d",
            "fix": {"kind": "no_go", "noGoId": 4}, "spans": [{"field": "body", "start": 1, "end": 3}],
        }])

    def test_an_unknown_code_cannot_be_raised(self):
        with self.assertRaises(ValueError):
            ApiProblem("teapot", "no")

    def test_an_unknown_path_is_a_not_found_envelope(self):
        self.assert_envelope(self.get("/api/no-such-thing"), 404, "not_found")

    def test_a_wrong_method_is_a_bad_request_envelope_and_keeps_allow(self):
        # An own-page write shape, so the guards let it through to the router (tests/test_api_guards.py).
        reply = self.client.delete("/api/ping", headers={"Content-Type": "application/json"})
        self.assert_envelope(reply, 405, "bad_request")
        self.assertIn("GET", reply.headers["allow"])

    def test_validation_error_names_the_field(self):
        reply = self.post("/api/probe/echo", {"fullName": "Ada"})
        body = self.assert_envelope(reply, 400, "bad_request")
        self.assertIn("personId", body["message"])
        self.assertNotIn("Ada", body["message"])

    def test_a_request_body_with_an_unknown_field_is_refused_by_name(self):
        body = self.assert_envelope(self.post("/api/probe/strict", {"firstName": "A", "nope": 1}), 400, "bad_request")
        self.assertIn("nope", body["message"])
        self.assertEqual(self.post("/api/probe/strict", {"first_name": "A"}).status_code, 200)

    def test_validation_error_names_a_query_field(self):
        body = self.assert_envelope(self.get("/api/probe/query?limit=abc"), 400, "bad_request")
        self.assertIn("limit", body["message"])

    def test_a_body_that_is_not_json_is_a_bad_request(self):
        reply = self.client.post(
            "/api/probe/echo", content="{not json", headers={"Content-Type": "application/json"}
        )
        body = self.assert_envelope(reply, 400, "bad_request")
        self.assertIn("body", body["message"])

    def test_validation_error_names_the_first_bad_field_only_once(self):
        body = self.assert_envelope(self.post("/api/probe/echo", {}), 400, "bad_request")
        self.assertEqual(body["message"].count("personId"), 1)

    def test_unhandled_error_is_a_clean_500_without_the_body(self):
        secret = "do-not-log-this-draft-text"
        with self.assertLogs("outreach.api", level="ERROR") as logged:
            reply = self.post("/api/probe/boom", {"draft": secret})
        self.assertEqual(reply.status_code, 500)
        self.assertEqual(
            reply.json(),
            {"code": "server_error", "message": "The server had a problem. Nothing was saved.", "checks": []},
        )
        formatter = logging.Formatter()
        text = "\n".join(formatter.format(record) for record in logged.records)
        self.assertNotIn(secret, text)
        self.assertNotIn(secret, reply.text)
        self.assertIn("RuntimeError", text)  # the traceback is there for debugging
        self.assertIn("/api/probe/boom", text)

    def test_a_500_does_not_leak_the_exception_text(self):
        with self.assertLogs("outreach.api", level="ERROR"):
            reply = self.post("/api/probe/boom", {})
        self.assertNotIn("internal detail", reply.text)


class WireTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.use_routers(zz_probes=PROBES)

    def test_wire_is_camel_case_both_ways(self):
        self.assertEqual(self.get("/api/probe/camel").json(), {"personId": 7, "fullName": "Ada"})
        camel = self.post("/api/probe/echo", {"personId": 1, "fullName": "A"})
        snake = self.post("/api/probe/echo", {"person_id": 2, "full_name": "B"})
        self.assertEqual(camel.json(), {"personId": 1, "fullName": "A"})
        self.assertEqual(snake.json(), {"personId": 2, "fullName": "B"})

    def test_a_default_is_still_emitted(self):
        self.assertEqual(self.post("/api/probe/echo", {"personId": 3}).json(), {"personId": 3, "fullName": "none"})


class ModelTest(unittest.TestCase):
    def test_wire_is_camel_case_both_ways_in_python(self):
        class Sample(Wire):
            person_id: int

        self.assertEqual(Sample(person_id=1).model_dump(by_alias=True), {"personId": 1})
        self.assertEqual(Sample.model_validate({"personId": 2}).person_id, 2)
        self.assertEqual(Sample.model_validate({"person_id": 3}).person_id, 3)

    def test_request_models_forbid_extra_fields_and_response_models_do_not_need_to(self):
        from outreach.api.models.base import WireRequest

        class In(WireRequest):
            person_id: int

        with self.assertRaises(Exception):
            In.model_validate({"personId": 1, "extra": 2})
        self.assertEqual(In.model_validate({"personId": 1}).person_id, 1)
        self.assertEqual(In.model_json_schema().get("additionalProperties"), False)

    def test_the_envelope_model_fields(self):
        self.assertEqual(set(ErrorEnvelope.model_fields), {"code", "message", "checks"})


if __name__ == "__main__":
    unittest.main()
