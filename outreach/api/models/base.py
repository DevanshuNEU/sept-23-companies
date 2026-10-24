"""The one base class every API model inherits."""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Wire(BaseModel):
    """camelCase on the wire both ways (snake_case is also accepted when parsing).

    `json_schema_serialization_defaults_required` makes a field with a default `required` in the response schema, so
    the generated TypeScript type has `x: T` and never `x?: T`: the API always sends every field.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        json_schema_serialization_defaults_required=True,
    )


class WireRequest(Wire):
    """Base for request bodies: an unknown field is a 400 that names it, never silently dropped."""

    model_config = ConfigDict(extra="forbid")
