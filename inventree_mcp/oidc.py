"""OIDC bearer authentication for the MCP endpoint.

For deployments where InvenTree signs users in through an external OpenID
provider (django-allauth SSO) and an MCP gateway in front of this plugin
authenticates clients against that same provider, then forwards the caller's
access token. This class verifies that token here and maps its subject to the
InvenTree user SSO already linked to it, through the allauth ``SocialAccount``
row created at login. No second credential is issued or stored.

It is one more DRF authentication class on ``MCPView`` next to Token, Basic
and OAuth2, so everything downstream is unchanged: the resolved user is bound
for the request and every tool call goes through ``proxy.call_view()`` with
that user's roles.

Off unless an issuer is configured. Settings (plugin settings, each
overridable by an ``INVENTREE_MCP_<KEY>`` environment variable so deployments
can keep them in config):

- ``OIDC_ISSUER``: expected ``iss``, compared exactly (a trailing slash
  matters).
- ``OIDC_AUDIENCE``: expected ``aud`` entry, usually the public URL of the
  MCP resource at the gateway.
- ``OIDC_PROVIDER``: allauth provider id whose ``SocialAccount.uid`` equals the
  token's ``sub``.
- ``OIDC_JWKS_URL`` (optional): signing keys; defaults to ``jwks_uri`` from the
  issuer's discovery document.
- ``OIDC_CLIENT_USERS`` (optional): ``client_id=username`` pairs for machine
  tokens (``sub == azp``, e.g. client credentials), which have no SSO link.
  Unlisted machine tokens are refused.

Only asymmetric algorithms are accepted.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

logger = logging.getLogger("inventree")

ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]
LEEWAY_SECONDS = 30

_jwk_clients: dict[str, Any] = {}
_discovery_failures: dict[str, float] = {}
DISCOVERY_RETRY_SECONDS = 30


@dataclass(frozen=True)
class OIDCConfig:
    """Resolved OIDC settings."""

    issuer: str
    audience: str
    provider: str
    jwks_url: str = ""
    client_users: dict[str, str] = field(default_factory=dict)


def _setting(key: str) -> str:
    env = os.environ.get(f"INVENTREE_MCP_{key}")
    if env is not None:
        return env.strip()
    from .settings import get_plugin_value

    return str(get_plugin_value(key, "") or "").strip()


def get_config() -> OIDCConfig | None:
    """Return the OIDC settings, or None when OIDC is off (no issuer).

    Raises AuthenticationFailed when an issuer is set but the rest is not, so
    a half-configured deployment refuses bearer JWTs instead of guessing.
    """
    issuer = _setting("OIDC_ISSUER")
    if not issuer:
        return None
    audience = _setting("OIDC_AUDIENCE")
    provider = _setting("OIDC_PROVIDER")
    if not audience or not provider:
        logger.error(
            "MCP OIDC: OIDC_ISSUER is set but OIDC_AUDIENCE or OIDC_PROVIDER is not"
        )
        raise exceptions.AuthenticationFailed("OIDC authentication is misconfigured")

    client_users: dict[str, str] = {}
    for pair in _setting("OIDC_CLIENT_USERS").split(","):
        client_id, sep, username = pair.partition("=")
        if sep and client_id.strip() and username.strip():
            client_users[client_id.strip()] = username.strip()
        elif pair.strip():
            logger.warning(
                "MCP OIDC: ignoring malformed OIDC_CLIENT_USERS entry %r", pair.strip()
            )

    return OIDCConfig(
        issuer, audience, provider, _setting("OIDC_JWKS_URL"), client_users
    )


def _jwks_url(config: OIDCConfig) -> str:
    if config.jwks_url:
        return config.jwks_url
    discovery = config.issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(discovery, timeout=5) as response:
        jwks_uri = json.load(response).get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri:
        raise exceptions.AuthenticationFailed("OIDC discovery document has no jwks_uri")
    return jwks_uri


def _jwk_client(config: OIDCConfig) -> Any:
    """Return the cached JWKS client for this issuer, building it on first use.

    Building it may fetch the discovery document. Any failure there (issuer
    unreachable, timeout, a malformed document) becomes a clean 401, not an
    unhandled exception, and is remembered for DISCOVERY_RETRY_SECONDS so an
    IdP outage doesn't cost every request another blocking fetch.
    """
    key = config.jwks_url or config.issuer
    if key in _jwk_clients:
        return _jwk_clients[key]

    failed_at = _discovery_failures.get(key)
    if failed_at is not None and time.monotonic() - failed_at < DISCOVERY_RETRY_SECONDS:
        raise exceptions.AuthenticationFailed("OIDC provider unavailable")

    from jwt import PyJWKClient

    try:
        jwks_url = _jwks_url(config)
    except exceptions.AuthenticationFailed:
        _discovery_failures[key] = time.monotonic()
        raise
    except Exception as exc:
        _discovery_failures[key] = time.monotonic()
        logger.warning("MCP OIDC discovery for %s failed: %s", config.issuer, exc)
        raise exceptions.AuthenticationFailed("OIDC provider unavailable") from exc

    _discovery_failures.pop(key, None)
    _jwk_clients[key] = PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)
    return _jwk_clients[key]


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2


class OIDCAuthentication(BaseAuthentication):
    """Authenticate ``Authorization: Bearer <jwt>`` issued by the configured OIDC provider."""

    keyword = "Bearer"

    def authenticate(self, request: Any) -> tuple[Any, dict[str, Any]] | None:
        auth = get_authorization_header(request).split()
        if len(auth) != 2 or auth[0].lower() != self.keyword.lower().encode():
            return None
        token = auth[1].decode(errors="replace")
        # InvenTree's own OAuth2 tokens are opaque; leave those to OAuth2Authentication.
        if not _looks_like_jwt(token):
            return None
        config = get_config()
        if config is None:
            return None

        claims = self.verify(token, config)
        user = self.user_for_claims(claims, config)
        if user is None:
            sub = str(claims.get("sub", ""))
            if sub and sub == claims.get("azp") and sub in config.client_users:
                raise exceptions.AuthenticationFailed(
                    "The user this client is mapped to in OIDC_CLIENT_USERS "
                    "does not exist or is inactive"
                )
            raise exceptions.AuthenticationFailed(
                "No InvenTree user is linked to this token's subject"
            )
        return user, claims

    def authenticate_header(self, request: Any) -> str:
        return 'Bearer realm="inventree-mcp"'

    def verify(
        self, token: str, config: OIDCConfig, jwk_client: Any = None
    ) -> dict[str, Any]:
        import jwt

        try:
            client = jwk_client or _jwk_client(config)
            signing_key = client.get_signing_key_from_jwt(token)
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=ALGORITHMS,
                audience=config.audience,
                issuer=config.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            logger.info("MCP OIDC token rejected: %s", exc)
            raise exceptions.AuthenticationFailed("Invalid or expired token") from exc

    def user_for_claims(self, claims: dict[str, Any], config: OIDCConfig) -> Any:
        from django.contrib.auth import get_user_model

        sub = str(claims.get("sub", ""))
        azp = claims.get("azp")
        if azp and sub == azp:
            username = config.client_users.get(sub)
            if username is None:
                logger.info(
                    "MCP OIDC: machine token for client %s has no mapped user", sub
                )
                return None
            return (
                get_user_model()
                .objects.filter(username=username, is_active=True)
                .first()
            )

        from allauth.socialaccount.models import SocialAccount

        account = (
            SocialAccount.objects
            .select_related("user")
            .filter(provider=config.provider, uid=sub)
            .first()
        )
        if account is None or not account.user.is_active:
            logger.info(
                "MCP OIDC: no active %s account linked to subject %s",
                config.provider,
                sub,
            )
            return None
        return account.user
