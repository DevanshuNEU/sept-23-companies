import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from outreach.api import openapi
from outreach.api.models.base import Wire
from tests.apitest import ApiCase

REGENERATE = "uv run python -m outreach.api.openapi --write && cd web && pnpm gen:api"
ERROR_STATUSES = ("400", "403", "404", "409", "422", "500", "503")

WRITER = '''
from fastapi import APIRouter
from outreach.api.models.base import Wire

router = APIRouter()


class Reply(Wire):
    thing_id: int
    note: str = ""


class Order(Wire):
    thing_id: int
    note: str = ""


@router.post("/things", response_model=Reply)
def create_thing(body: Order) -> Reply:
    return Reply(thing_id=body.thing_id)


@router.put("/things/{thing_id}", response_model=Reply)
def replace_thing(thing_id: int, body: Order) -> Reply:
    return Reply(thing_id=thing_id)


@router.delete("/things/{thing_id}", response_model=Reply)
def delete_thing(thing_id: int) -> Reply:
    return Reply(thing_id=thing_id)


@router.get("/things/{thing_id}", response_model=Reply)
def read_thing(thing_id: int) -> Reply:
    return Reply(thing_id=thing_id)
'''


class SnapshotTest(unittest.TestCase):
    def test_committed_schema_is_current(self):
        committed = openapi.OPENAPI_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            committed, openapi.render(openapi.build_schema()),
            f"web/openapi.json is out of date. Run: {REGENERATE}",
        )

    def test_the_check_command_passes_on_the_committed_file(self):
        self.assertEqual(openapi.main(["--check"]), 0)

    def test_the_check_command_fails_with_the_regenerate_command(self):
        with tempfile.TemporaryDirectory() as folder:
            stale = Path(folder) / "openapi.json"
            stale.write_text("{}\n", encoding="utf-8")
            err = io.StringIO()
            with mock.patch.object(openapi, "OPENAPI_PATH", stale), redirect_stderr(err):
                self.assertEqual(openapi.main(["--check"]), 1)
            self.assertIn(REGENERATE, err.getvalue())
            self.assertEqual(stale.read_text(encoding="utf-8"), "{}\n")  # --check never writes

    def test_the_check_command_fails_when_the_file_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            err = io.StringIO()
            with mock.patch.object(openapi, "OPENAPI_PATH", Path(folder) / "none.json"), redirect_stderr(err):
                self.assertEqual(openapi.main(["--check"]), 1)
            self.assertIn(REGENERATE, err.getvalue())

    def test_write_produces_sorted_indented_json_that_check_accepts(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "openapi.json"
            with mock.patch.object(openapi, "OPENAPI_PATH", target), redirect_stdout(io.StringIO()):
                self.assertEqual(openapi.main(["--write"]), 0)
                text = target.read_text(encoding="utf-8")
                self.assertEqual(openapi.main(["--check"]), 0)
        data = json.loads(text)
        self.assertEqual(text, json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n")

    def test_the_schema_does_not_depend_on_the_environment(self):
        with mock.patch.dict("os.environ", {"OUTREACH_DB": "/nowhere/else.db", "OUTREACH_API_PORT": "3999"}):
            self.assertEqual(openapi.render(openapi.build_schema()), openapi.OPENAPI_PATH.read_text(encoding="utf-8"))

    def test_no_dashes_in_the_committed_schema(self):
        text = openapi.OPENAPI_PATH.read_text(encoding="utf-8")
        self.assertFalse(any(c in text for c in (chr(0x2013), chr(0x2014))))


class ShapeTest(ApiCase):
    def setUp(self):
        super().setUp()
        self.use_routers(zz_writer=WRITER)
        self.schema = openapi.build_schema(self.app)

    def operations(self):
        for path, item in self.schema["paths"].items():
            for method, op in item.items():
                yield path, method, op

    def test_every_operation_id_is_unique_and_is_the_function_name(self):
        ids = [op["operationId"] for _, _, op in self.operations()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(
            {"ping", "create_thing", "replace_thing", "delete_thing", "read_thing"}, set(ids)
        )
        for path, method, op in self.operations():
            self.assertNotIn("api", op["operationId"].split("_"), (path, method))  # not FastAPI's long default

    def test_no_schema_name_has_an_input_or_output_suffix(self):
        # A response-only model must not split into X-Input and X-Output.
        names = self.schema["components"]["schemas"]
        self.assertIn("Reply", names)
        for name in names:
            self.assertFalse(name.endswith(("-Input", "-Output")), name)

    def test_a_model_used_in_both_directions_does_not_split_either(self):
        self.assertNotIn("Order-Input", self.schema["components"]["schemas"])

    def test_defaults_are_required_in_generated_models(self):
        class Throwaway(Wire):
            seen: bool = False

        self.assertIn("seen", Throwaway.model_json_schema(mode="serialization")["required"])
        reply = self.schema["components"]["schemas"]["Reply"]
        self.assertEqual(sorted(reply["required"]), ["note", "thingId"])

    def test_every_write_operation_documents_the_error_envelope(self):
        writes = [(p, m, op) for p, m, op in self.operations() if m in ("post", "put", "patch", "delete")]
        self.assertEqual(len(writes), 3)
        for path, method, op in writes:
            for status in ERROR_STATUSES:
                with self.subTest(path=path, method=method, status=status):
                    ref = op["responses"][status]["content"]["application/json"]["schema"]["$ref"]
                    self.assertEqual(ref, "#/components/schemas/ErrorEnvelope")

    def test_reads_document_the_errors_they_can_give(self):
        for path, method, op in self.operations():
            if method == "get":
                for status in ("400", "403", "404", "500", "503"):
                    self.assertIn(status, op["responses"], (path, status))
                for status in ("409", "422"):
                    self.assertNotIn(status, op["responses"], (path, status))

    def test_there_is_no_framework_validation_error_shape(self):
        # Validation failures are the bad_request envelope, so FastAPI's own 422 shape must not be advertised.
        names = self.schema["components"]["schemas"]
        self.assertNotIn("HTTPValidationError", names)
        self.assertNotIn("ValidationError", names)
        self.assertIn("ErrorEnvelope", names)
        self.assertIn("Check", names)
        self.assertEqual(sorted(names["ErrorEnvelope"]["required"]), ["checks", "code", "message"])

    def test_the_schema_is_served_by_the_app_object_not_a_public_route(self):
        self.assertEqual(self.get("/openapi.json").status_code, 404)
        self.assertEqual(self.get("/docs").status_code, 404)


if __name__ == "__main__":
    unittest.main()
