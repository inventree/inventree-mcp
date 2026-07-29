"""Describe a view's optional output-expansion flags (e.g. `part_detail`).

Many InvenTree list/detail views can inline a related object's full detail
into the response on request (`?part_detail=true` on the stock API embeds
the full Part, avoiding a separate get_part call) rather than only exposing
its ID. These flags are declared once per view as `output_options`, an
`InvenTree.fields.OutputConfiguration` subclass listing `InvenTreeOutputOption`
instances (flag name, default, description) - the same metadata InvenTree's
own OpenAPI schema is generated from (see `InvenTree.schema.
schema_for_view_output_options`). Reading it here means this can't drift
from the real API the way a hand-maintained list per resource would.

Not every view declares `output_options` (flat resources with no
expandable relations, e.g. Company, Attachment, don't need it) -
describe_output_options() returns an empty dict for those rather than
raising, same permissive spirit as filter_introspection.py.
"""

from __future__ import annotations

from typing import Any


def describe_output_options(view_cls: type) -> dict[str, Any]:
    """Describe the optional output-expansion flags available on *view_cls*.

    Args:
        view_cls: a list or detail view class as used in the real API
            urlconf (e.g. stock.api.StockList or stock.api.StockDetail).
            Both a resource's list and detail view conventionally share the
            same `output_options` class, so either works.

    Returns:
        A dict of {flag_name: {default_included, description}} - each key
        is a boolean query parameter that can be passed directly in a
        list/get tool's `filters` argument, e.g. filters={"part_detail": true}.
        Empty if the view declares no `output_options`.
    """
    output_config = getattr(view_cls, "output_options", None)
    if output_config is None:
        return {}

    return {
        option.flag: {
            "default_included": bool(option.default),
            "description": option.description,
        }
        for option in output_config.OPTIONS
    }
