"""MCP tools for querying InvenTree Company/Contact/Address data."""

from __future__ import annotations

from typing import Any

from ..mcp_server import mcp
from ..proxy import call_view
from ..view_resolution import resolve_view
from ._common import build_query_params


@mcp.tool()
async def list_companies(
    search: str | None = None,
    is_customer: bool | None = None,
    is_supplier: bool | None = None,
    is_manufacturer: bool | None = None,
    active: bool | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List companies (suppliers, customers, and/or manufacturers).

    A single Company can be more than one of these at once (e.g. both a
    supplier and a manufacturer) - the three boolean flags are independent,
    not mutually exclusive categories.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_company's return value -
    use its `pk` field with get_company to fetch full detail for one of them.

    Args:
        search: free-text search against name, description, website, and tax ID.
        is_customer: restrict to companies flagged as a customer.
        is_supplier: restrict to companies flagged as a supplier.
        is_manufacturer: restrict to companies flagged as a manufacturer.
        active: True for only active companies, False for only inactive
            ones, omit to include both.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("company") and check
            its ordering_fields list for valid values - an unrecognized
            field is silently ignored (no error, no sort) rather than
            rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("company") to see what's
            available.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if search is not None:
        base["search"] = search
    if is_customer is not None:
        base["is_customer"] = is_customer
    if is_supplier is not None:
        base["is_supplier"] = is_supplier
    if is_manufacturer is not None:
        base["is_manufacturer"] = is_manufacturer
    if active is not None:
        base["active"] = active
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("company.api", "CompanyList"),
        "GET",
        "/api/company/",
        query_params=params,
    )


@mcp.tool()
async def get_company(company_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single company by its ID.

    Returns the same object shape as one entry in list_companies's
    `results` array. Get a valid ID from list_companies (its `pk` field) if
    you don't already have one. Use list_contacts/list_addresses (filtering
    by `company`) to see this company's contacts and addresses.

    Args:
        company_id: the Company's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("company") and check its optional_fields.

    Raises:
        ToolError: no company exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("company.api", "CompanyDetail"),
        "GET",
        f"/api/company/{company_id}/",
        pk=company_id,
        query_params=filters,
    )


@mcp.tool()
async def list_contacts(
    company: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List contacts (people) at companies.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_contact's return value -
    use its `pk` field with get_contact to fetch full detail for one of them.

    Args:
        company: restrict to contacts at this Company ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("contact") and check
            its ordering_fields list for valid values - an unrecognized
            field is silently ignored (no error, no sort) rather than
            rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("contact") to see what's
            available.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if company is not None:
        base["company"] = company
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("company.api", "ContactList"),
        "GET",
        "/api/company/contact/",
        query_params=params,
    )


@mcp.tool()
async def get_contact(contact_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single contact by its ID.

    Returns the same object shape as one entry in list_contacts's `results`
    array. Get a valid ID from list_contacts (its `pk` field) if you don't
    already have one.

    Args:
        contact_id: the Contact's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("contact") and check its optional_fields.

    Raises:
        ToolError: no contact exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("company.api", "ContactDetail"),
        "GET",
        f"/api/company/contact/{contact_id}/",
        pk=contact_id,
        query_params=filters,
    )


@mcp.tool()
async def list_addresses(
    company: int | None = None,
    ordering: str | None = None,
    filters: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List company addresses.

    Returns a paginated envelope: {count, next, previous, results}. Each
    entry in `results` has the same shape as get_address's return value -
    use its `pk` field with get_address to fetch full detail for one of them.

    Args:
        company: restrict to addresses belonging to this Company ID.
        ordering: field to sort results by ('-' prefix for descending, omit
            it for ascending). Call describe_filters("address") and check
            its ordering_fields list for valid values - an unrecognized
            field is silently ignored (no error, no sort) rather than
            rejected.
        filters: additional filter parameters beyond the named arguments
            above - call describe_filters("address") to see what's
            available.
        limit: maximum number of results to return - defaults to 100 (the
            maximum) to minimize round trips for large result sets; pass a
            smaller value to page through results in smaller batches.
        offset: pagination offset.
    """
    base: dict[str, Any] = {}
    if company is not None:
        base["company"] = company
    if ordering is not None:
        base["ordering"] = ordering

    params = build_query_params(base, filters, limit, offset)

    return await call_view(
        resolve_view("company.api", "AddressList"),
        "GET",
        "/api/company/address/",
        query_params=params,
    )


@mcp.tool()
async def get_address(address_id: int, filters: dict[str, Any] | None = None) -> dict:
    """Get full detail for a single company address by its ID.

    Returns the same object shape as one entry in list_addresses's
    `results` array. Get a valid ID from list_addresses (its `pk` field) if
    you don't already have one.

    Args:
        address_id: the Address's database ID.
        filters: optional-field toggles beyond what's returned by default -
            call describe_filters("address") and check its optional_fields.

    Raises:
        ToolError: no address exists with that ID, or the caller doesn't
            have permission to view it.
    """
    return await call_view(
        resolve_view("company.api", "AddressDetail"),
        "GET",
        f"/api/company/address/{address_id}/",
        pk=address_id,
        query_params=filters,
    )
