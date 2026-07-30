"""Derive MCP tool output schemas from the real InvenTree DRF serializers.

MCPServer can only auto-derive a tool's outputSchema from its Python return
type annotation, and our tools return a bare `dict` (the actual shape comes
from InvenTree's serializers and varies per endpoint) - so by default no
output schema is reported at all. Rather than hand-write and maintain a
TypedDict/Pydantic model per tool (which would silently drift from the real
API as serializers evolve), this module builds a JSON Schema directly from
the serializer's own fields - the same source of truth proxy.call_view()
already guarantees the *data* matches.

This is a best-effort translation of DRF's field taxonomy into JSON Schema,
not a byte-perfect one: fields we don't have a specific mapping for (e.g.
SerializerMethodField, ReadOnlyField - their real type can't be determined
without evaluating the method) fall back to an unconstrained schema ({})
rather than guessing wrong. Good enough for a calling agent to understand
the general shape; not a validation contract.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf

_MAX_NESTING_DEPTH = 4

# Ordered most-specific-subclass-first, so e.g. EmailField (a CharField
# subclass) is matched before the generic CharField entry below it.
_ORDERED_FIELD_SCHEMAS: list[tuple[type, dict[str, Any]]] = [
    (drf.EmailField, {"type": "string", "format": "email"}),
    (drf.URLField, {"type": "string", "format": "uri"}),
    (drf.UUIDField, {"type": "string", "format": "uuid"}),
    (drf.IPAddressField, {"type": "string"}),
    (drf.SlugField, {"type": "string"}),
    (drf.RegexField, {"type": "string"}),
    (drf.FilePathField, {"type": "string"}),
    (
        drf.MultipleChoiceField,
        {
            "type": "array",
            "items": {"type": ["string", "integer", "number", "boolean"]},
        },
    ),
    (drf.ChoiceField, {"type": ["string", "integer", "number", "boolean"]}),
    # "uri-reference" (not "uri"): FileField/ImageField commonly serialize as
    # a server-relative media path (e.g. "/media/part_images/0402.jpg"), which
    # isn't a valid absolute "uri".
    (
        drf.FileField,
        {"type": "string", "format": "uri-reference"},
    ),  # also covers ImageField
    (drf.BooleanField, {"type": "boolean"}),
    # No "format": "date-time" here - InvenTree's REST_FRAMEWORK DATETIME_FORMAT
    # setting overrides DRF's default ISO-8601 output to "%Y-%m-%d %H:%M" for
    # every DateTimeField, which doesn't satisfy JSON Schema's date-time format.
    (drf.DateTimeField, {"type": "string"}),
    (drf.DateField, {"type": "string", "format": "date"}),
    (drf.TimeField, {"type": "string", "format": "time"}),
    (drf.DecimalField, {"type": ["string", "number"]}),
    (drf.FloatField, {"type": "number"}),
    (drf.IntegerField, {"type": "integer"}),
    (drf.PrimaryKeyRelatedField, {"type": "integer"}),
    (drf.RelatedField, {"type": "string"}),
    (drf.ListField, {"type": "array"}),
    (drf.DictField, {"type": "object"}),
    (drf.JSONField, {}),
    (drf.CharField, {"type": "string"}),
]


def _build_properties(fields: dict[str, drf.Field], depth: int) -> dict[str, Any]:
    return {name: _field_schema(field, depth) for name, field in fields.items()}


def _field_schema(field: drf.Field, depth: int = 0) -> dict[str, Any]:
    """Best-effort JSON Schema fragment for a single serializer field."""
    if depth >= _MAX_NESTING_DEPTH:
        return {"type": "object"}

    if isinstance(field, drf.ListSerializer):
        return {"type": "array", "items": _field_schema(field.child, depth + 1)}

    if isinstance(field, drf.ManyRelatedField):
        return {
            "type": "array",
            "items": _field_schema(field.child_relation, depth + 1),
        }

    if isinstance(field, drf.BaseSerializer):
        # Nested serializer fields (e.g. `customer_detail`, `responsible_detail`)
        # are commonly None - the related FK itself is null, or the detail
        # wasn't requested via an output option - so this needs the same
        # unconditional-nullable treatment as concrete field types below.
        return {
            "type": ["object", "null"],
            "properties": _build_properties(field.fields, depth + 1),
        }

    result: dict[str, Any] = {}
    for field_type, schema in _ORDERED_FIELD_SCHEMAS:
        if isinstance(field, field_type):
            result = dict(schema)
            break

    # DRF's `allow_null` governs *input* validation, not what to_representation()
    # can actually emit - a field can be allow_null=False while its underlying
    # model column is nullable. Trusting allow_null isn't safe, so every
    # concrete type is nullable unconditionally.
    if isinstance(result.get("type"), str):
        result["type"] = [result["type"], "null"]
    elif isinstance(result.get("type"), list) and "null" not in result["type"]:
        result["type"] = [*result["type"], "null"]

    if "format" in result:
        # A format-constrained string type can still legitimately come back
        # blank ("") rather than null - DRF's allow_blank fields commonly use
        # "" as their "not set" sentinel instead of (or alongside) None, and
        # "" never matches a format like "uri"/"email"/"date". Express that as
        # an explicit alternative rather than dropping the format annotation.
        result = {
            "anyOf": [
                {"type": "null"},
                {"const": ""},
                {"type": "string", "format": result["format"]},
            ]
        }

    return result


def serializer_schema(serializer_class: type[drf.Serializer]) -> dict[str, Any]:
    """Build a JSON Schema object describing an instance of *serializer_class*."""
    try:
        instance = serializer_class()
    except TypeError:
        # Some serializers require constructor args we can't supply outside a
        # real request/view context - fall back to an unconstrained object
        # rather than failing tool registration.
        return {"type": "object"}

    return {
        "type": "object",
        "title": serializer_class.__name__,
        "properties": _build_properties(instance.fields, 0),
    }


def paginated_schema(serializer_class: type[drf.Serializer]) -> dict[str, Any]:
    """JSON Schema for a DRF LimitOffsetPagination response wrapping *serializer_class*."""
    return {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "next": {"type": ["string", "null"], "format": "uri"},
            "previous": {"type": ["string", "null"], "format": "uri"},
            "results": {
                "type": "array",
                "items": serializer_schema(serializer_class),
            },
        },
        "required": ["count", "next", "previous", "results"],
    }
