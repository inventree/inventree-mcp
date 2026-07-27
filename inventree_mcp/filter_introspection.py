"""Describe a DRF list view's real filter/search/ordering options.

Mirrors schema_introspection.py's approach for output shapes: rather than
hand-maintain a description of what's filterable per resource (which would
drift as InvenTree's FilterSet classes evolve), this reads the same metadata
django-filter itself uses (view.filterset_class.base_filters) plus DRF's
search_fields/ordering_fields, and turns it into a plain dict a calling
agent can use directly as the `filters` argument on a list_* tool.

Some views (e.g. company.api.ContactList/AddressList) skip a full
filterset_class and instead use DRF's `filterset_fields = [...]` shorthand,
which makes django_filter's DjangoFilterBackend auto-build an exact-match
FilterSet at request time - the real API genuinely accepts these as query
params, but there's no filterset_class for us to introspect. _model_field_filters()
covers that case by reading the field type off the view's own model instead.
"""

from __future__ import annotations

from typing import Any

import django_filters as df
from django.core.exceptions import FieldDoesNotExist

# Best-effort Django model field internal-type -> our type string, used only
# as a fallback for plain `filterset_fields` (no explicit FilterSet class to
# read a real django_filters.Filter subclass off of). Not exhaustive - falls
# back to "string" for anything unlisted, same permissive spirit as
# _filter_type_name() below.
_MODEL_FIELD_TYPES: dict[str, str] = {
    "ForeignKey": "integer (id)",
    "OneToOneField": "integer (id)",
    "BooleanField": "boolean",
    "IntegerField": "number",
    "PositiveIntegerField": "number",
    "FloatField": "number",
    "DecimalField": "number",
    "DateTimeField": "string (date-time)",
    "DateField": "string (date)",
    "TimeField": "string (time)",
    "UUIDField": "string (uuid)",
}

# Ordered most-specific-subclass-first: e.g. ModelChoiceFilter is a
# ChoiceFilter subclass and must be matched before the generic entry below it.
_ORDERED_FILTER_TYPES: list[tuple[type, str]] = [
    (df.BooleanFilter, "boolean"),
    (df.ModelMultipleChoiceFilter, "array of integer (id)"),
    (df.ModelChoiceFilter, "integer (id)"),
    (df.MultipleChoiceFilter, "array of string (choice)"),
    (df.ChoiceFilter, "string (choice)"),
    (df.NumberFilter, "number"),
    (df.DateTimeFilter, "string (date-time)"),
    (df.DateFilter, "string (date)"),
    (df.TimeFilter, "string (time)"),
    (df.UUIDFilter, "string (uuid)"),
    (df.CharFilter, "string"),
]


def _filter_type_name(f: df.Filter) -> str:
    for filter_type, name in _ORDERED_FILTER_TYPES:
        if isinstance(f, filter_type):
            return name
    return "string"


def _filter_info(f: df.Filter) -> dict[str, Any]:
    info: dict[str, Any] = {"type": _filter_type_name(f)}

    if f.label:
        info["label"] = str(f.label)

    choices = getattr(f, "extra", {}).get("choices")
    if choices:
        info["choices"] = [value for value, _display in choices]

    return info


def _model_field_filters(view_cls: type) -> dict[str, Any]:
    """Best-effort filter info for views using DRF's `filterset_fields` shorthand.

    No django_filters.Filter instances exist to introspect here - only a
    list of field names DjangoFilterBackend auto-generates exact-match
    filters from at request time - so this reads the type straight off the
    view's own model instead.
    """
    field_names = getattr(view_cls, "filterset_fields", None) or []
    model = getattr(getattr(view_cls, "queryset", None), "model", None)
    if not field_names or model is None:
        return {}

    filters = {}
    for name in field_names:
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            filters[name] = {"type": "string"}
            continue

        filters[name] = {
            "type": _MODEL_FIELD_TYPES.get(field.get_internal_type(), "string")
        }

    return filters


def _default_ordering_fields(view_cls: type) -> list[str]:
    """Best-effort reproduction of DRF's own fallback for views that don't declare `ordering_fields`.

    `rest_framework.filters.OrderingFilter.get_default_valid_fields()` doesn't
    treat a missing `ordering_fields` as "no ordering allowed" - it allows
    ordering by any non-write-only field on the view's serializer instead.
    Before this, `describe_filterset()` read `getattr(view_cls,
    'ordering_fields', None) or []`, which couldn't distinguish "not declared"
    from "declared empty" and reported zero ordering fields either way - e.g.
    `describe_filters("bom_substitute")` claimed no ordering was possible even
    though `ordering=part` genuinely works against the real API
    (`BomItemSubstituteList` doesn't declare `ordering_fields`). Mirrors DRF's
    logic rather than calling it directly: DRF's version needs a bound view
    *instance* (`view.get_serializer_class()`), but this module only ever has
    the view *class* to introspect with, same constraint as the rest of this
    file.
    """
    serializer_class = getattr(view_cls, "serializer_class", None)
    if serializer_class is None:
        return []

    try:
        fields = serializer_class().fields
    except (TypeError, AssertionError):
        # TypeError: constructor needs args this bare instantiation can't
        # supply. AssertionError: DRF's own internal checks (e.g. a
        # ModelSerializer subclass missing Meta.model). Fail soft to "no
        # ordering fields reported" rather than raising out of a pure
        # introspection helper.
        return []

    ordering_fields: list[str] = []
    for field in fields.values():
        if getattr(field, "write_only", False) or field.source == "*":
            continue
        source = field.source.replace(".", "__")
        if source not in ordering_fields:
            ordering_fields.append(source)

    return ordering_fields


def describe_filterset(view_cls: type) -> dict[str, Any]:
    """Describe the search/filter/ordering options available on *view_cls*.

    Args:
        view_cls: a list view class as used in the real API urlconf (e.g.
            part.api.PartList).

    Returns:
        A dict with:
        - search_fields: fields matched by a free-text `search` query param.
        - ordering_fields: fields usable as the corresponding list tool's
          `ordering` argument (prefix with '-' for descending). If the view
          doesn't declare `ordering_fields` at all, this reports DRF's own
          fallback (any readable serializer field) rather than an empty list
          - see _default_ordering_fields().
        - filters: {name: {type, label, choices}} - each key is usable
          directly as a query parameter / filters dict entry.
    """
    filterset_class = getattr(view_cls, "filterset_class", None)

    if filterset_class is not None:
        filters = {
            name: _filter_info(f) for name, f in filterset_class.base_filters.items()
        }
    else:
        filters = _model_field_filters(view_cls)

    declared_ordering = getattr(view_cls, "ordering_fields", None)
    ordering_fields = (
        list(declared_ordering)
        if declared_ordering is not None
        else _default_ordering_fields(view_cls)
    )

    return {
        "search_fields": list(getattr(view_cls, "search_fields", None) or []),
        "ordering_fields": ordering_fields,
        "filters": filters,
    }
