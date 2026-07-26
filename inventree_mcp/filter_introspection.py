"""Describe a DRF list view's real filter/search/ordering options.

Mirrors schema_introspection.py's approach for output shapes: rather than
hand-maintain a description of what's filterable per resource (which would
drift as InvenTree's FilterSet classes evolve), this reads the same metadata
django-filter itself uses (view.filterset_class.base_filters) plus DRF's
search_fields/ordering_fields, and turns it into a plain dict a calling
agent can use directly as the `filters` argument on a list_* tool.
"""

from __future__ import annotations

from typing import Any

import django_filters as df

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


def describe_filterset(view_cls: type) -> dict[str, Any]:
    """Describe the search/filter/ordering options available on *view_cls*.

    Args:
        view_cls: a list view class as used in the real API urlconf (e.g.
            part.api.PartList).

    Returns:
        A dict with:
        - search_fields: fields matched by a free-text `search` query param.
        - ordering_fields: fields usable via an `ordering` filter (prefix
          with '-' for descending).
        - filters: {name: {type, label, choices}} - each key is usable
          directly as a query parameter / filters dict entry.
    """
    filterset_class = getattr(view_cls, "filterset_class", None)

    filters = {}
    if filterset_class is not None:
        filters = {
            name: _filter_info(f) for name, f in filterset_class.base_filters.items()
        }

    return {
        "search_fields": list(getattr(view_cls, "search_fields", None) or []),
        "ordering_fields": list(getattr(view_cls, "ordering_fields", None) or []),
        "filters": filters,
    }
