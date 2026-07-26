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
untouched. Remove this workaround if/when the core bugs are fixed.
"""

from __future__ import annotations

from typing import Any

from InvenTree.permissions import (
    InvenTreeRoleScopeMixin,
    InvenTreeTokenMatchesOASRequirements,
)
from rest_framework.settings import api_settings
from users.authentication import ExtendedOAuth2Authentication

_scope_resolver = InvenTreeRoleScopeMixin()


def authentication_classes_for(user: Any, oauth2_token: Any) -> list[type]:
    """Return an authentication_classes list that resolves to (user, oauth2_token)."""

    class _PreResolvedOAuth2Authentication(ExtendedOAuth2Authentication):
        def authenticate(self, request):
            return (user, oauth2_token)

    return [_PreResolvedOAuth2Authentication]


class _FixedTokenMatchesOASRequirements(InvenTreeTokenMatchesOASRequirements):
    """InvenTreeTokenMatchesOASRequirements without the broken super() fallback (bug 2 above)."""

    def has_permission(self, request, view):
        if self.is_oauth2ed(request):
            return self.check_oauth2_authentication(request, view)

        return bool(request.user and request.user.is_authenticated)


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


def scoped_view_class(view_cls: type) -> type:
    """Return a subclass of view_cls with the OAuth2 scope-check workarounds applied.

    Without this, a genuinely OAuth2-authenticated request either raises
    ImproperlyConfigured regardless of scope (bug 1), or crashes instead of
    being cleanly denied when the scope doesn't match (bug 2) - see the
    module docstring.
    """
    required_alternate_scopes = _scope_resolver.get_required_alternate_scopes(
        None, view_cls()
    )
    return type(
        f"_ScopeWorkaround_{view_cls.__name__}",
        (view_cls,),
        {
            "required_alternate_scopes": required_alternate_scopes,
            "permission_classes": _fixed_permission_classes(view_cls),
        },
    )
