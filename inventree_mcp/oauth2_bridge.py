"""Re-present an already-validated OAuth2 token to a proxied DRF view.

mcp_transport.py runs real DRF authentication on the incoming MCP request
(including OAuth2), so by the time a tool calls proxy.call_view() we already
have the genuine (user, access_token) pair - re-validating it would be
redundant. What we need instead is a way to make the *proxied* view's
authentication layer see that same pair, as a real
oauth2_provider OAuth2Authentication instance (not force_authenticate()'s
synthetic ForcedAuthentication, which InvenTreeTokenMatchesOASRequirements
does not recognize as OAuth2 at all - see InvenTree.permissions.is_oauth2ed).

DRF authentication_classes must be a list of zero-arg-instantiable classes,
so authentication_classes_for() builds one on the fly per call, closing over
the resolved user/token.

Works around two independent InvenTree core bugs in the OAuth2 permission
chain (InvenTree/permissions.py), both reproduced directly against
part.api.PartList with no plugin code involved - they affect the real REST
API too, not just MCP:

1. OASTokenMixin.check_oauth2_authentication() instantiates the *raw*
   oauth2_provider.TokenMatchesOASRequirements() directly rather than going
   through `self`, so it never reaches InvenTree's own dynamic
   get_required_alternate_scopes() (InvenTreeRoleScopeMixin). Since no
   InvenTree view defines a static required_alternate_scopes attribute,
   *any* genuinely OAuth2-authenticated request raises ImproperlyConfigured
   before the scope is even checked:

       >>> PartList.as_view()(factory.get('/api/part/', HTTP_AUTHORIZATION='Bearer <real-token>'))
       500 - "TokenMatchesOASRequirements requires the view to define the
       required_alternate_scopes attribute"

2. Once (1) is worked around, a token that's validly authenticated but
   lacks the required scope should be cleanly denied (403) - instead it
   crashes, because OASTokenMixin.has_permission()'s fallback branch calls
   super().has_permission(request, view), and neither OASTokenMixin nor
   InvenTreeRoleScopeMixin/InvenTreeTokenMatchesOASRequirements actually
   inherits from rest_framework.permissions.BasePermission - there is
   nothing for that super() call to reach:

       AttributeError: 'super' object has no attribute 'has_permission'

scoped_view_class() below works around both: it pre-computes the same
required_alternate_scopes value InvenTree's own dynamic logic would produce
and attaches it as a static attribute (fixes 1), and swaps
InvenTreeTokenMatchesOASRequirements for a corrected subclass that skips the
broken super() fallback (fixes 2). Everything else in the view's normal
permission stack (IsAuthenticated, ModelPermission, RolePermission) is left
untouched.

Both bugs are fixed in InvenTree 1.7.0 and later, where scoped_view_class()
returns the view unchanged (see _CORE_NEEDS_WORKAROUND). The workaround can be
deleted outright once the plugin's MIN_VERSION reaches 1.7.0.
"""

from __future__ import annotations

from typing import Any

from InvenTree.permissions import (
    InvenTreeRoleScopeMixin,
    InvenTreeTokenMatchesOASRequirements,
    OASTokenMixin,
    map_scope,
)
from rest_framework.settings import api_settings
from users.authentication import ExtendedOAuth2Authentication

_scope_resolver = InvenTreeRoleScopeMixin()

# Both core bugs described above were fixed in InvenTree 1.7.0, which replaced
# OASTokenMixin.check_oauth2_authentication() with a scope check that runs
# through each permission class's own get_required_alternate_scopes(). On
# those versions the workaround is not just redundant but broken (it calls
# the removed method), so feature-detect and leave the view untouched.
_CORE_NEEDS_WORKAROUND = hasattr(OASTokenMixin, "check_oauth2_authentication")


def authentication_classes_for(user: Any, oauth2_token: Any) -> list[type]:
    """Return an authentication_classes list that resolves to (user, oauth2_token)."""

    class _PreResolvedOAuth2Authentication(ExtendedOAuth2Authentication):
        def authenticate(self, request):
            return (user, oauth2_token)

    return [_PreResolvedOAuth2Authentication]


class _FixedTokenMatchesOASRequirements(InvenTreeTokenMatchesOASRequirements):
    """InvenTreeTokenMatchesOASRequirements without the broken super() fallback (bug 2 above).

    In InvenTree 1.7.0+, the bugs are fixed in the parent class, so this just delegates.
    In earlier versions, check_oauth2_authentication() exists and is called here.
    """

    def has_permission(self, request, view):
        if not _CORE_NEEDS_WORKAROUND:
            # Bugs are fixed in the parent; use its normal permission check
            return super().has_permission(request, view)

        # Original workaround for older InvenTree versions
        if self.is_oauth2ed(request):
            return self.check_oauth2_authentication(request, view)

        return bool(request.user and request.user.is_authenticated)

    # Mark this method as already enforcing OAuth2 scopes to prevent double-wrapping
    has_permission.enforces_oauth2_scopes = True


def _fixed_permission_classes(view_cls: type) -> list[type]:
    classes = getattr(view_cls, "permission_classes", None) or list(
        api_settings.DEFAULT_PERMISSION_CLASSES
    )
    return [
        _FixedTokenMatchesOASRequirements
        if cls is InvenTreeTokenMatchesOASRequirements
        else cls
        for cls in classes
    ]


def _required_alternate_scopes(view_cls: type) -> dict:
    """Pre-compute the OAuth2 scopes a request to view_cls must carry.

    Normally the role-derived scopes InvenTreeRoleScopeMixin would compute.
    But for a model with no role mapping that resolver falls back to the
    generic read scope ("g:read"), which would be *looser* than what some
    views' own permission class demands - e.g. the audit log's
    IsAdminOrAdminScope requires "a:staff". In that case, defer to the
    first OAuth2-aware, non-role-based permission class on the view, if it
    declares its own scopes.
    """
    view = view_cls()
    role_scopes = _scope_resolver.get_required_alternate_scopes(None, view)

    if role_scopes != map_scope(only_read=True):
        return role_scopes

    for cls in getattr(view_cls, "permission_classes", None) or []:
        if (
            issubclass(cls, OASTokenMixin)
            and not issubclass(cls, InvenTreeRoleScopeMixin)
            and hasattr(cls, "get_required_alternate_scopes")
        ):
            return cls().get_required_alternate_scopes(None, view)

    return role_scopes


def scoped_view_class(view_cls: type) -> type:
    """Return a subclass of view_cls with the OAuth2 scope-check workarounds applied.

    Without this, a genuinely OAuth2-authenticated request either raises
    ImproperlyConfigured regardless of scope (bug 1), or crashes instead of
    being cleanly denied when the scope doesn't match (bug 2) - see the
    module docstring.
    """
    if not _CORE_NEEDS_WORKAROUND:
        return view_cls

    required_alternate_scopes = _required_alternate_scopes(view_cls)
    return type(
        f"_ScopeWorkaround_{view_cls.__name__}",
        (view_cls,),
        {
            "required_alternate_scopes": required_alternate_scopes,
            "permission_classes": _fixed_permission_classes(view_cls),
        },
    )
