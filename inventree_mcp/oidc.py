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
  issuer's discovery document. Discovery needs an https issuer and an https
  ``jwks_uri``; set this to fetch keys from a plain http address (an
  in-cluster one, say).
- ``OIDC_CLIENT_USERS`` (optional): ``client_id=username`` pairs for machine
  tokens (``sub == azp``, e.g. client credentials), which have no SSO link.
  Unlisted machine tokens are refused.

Only asymmetric algorithms are accepted.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK, PyJWKClient
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

logger = logging.getLogger("inventree")

ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]
LEEWAY_SECONDS = 30

_jwk_clients: dict[str, Any] = {}
_discovery_failures: dict[str, float] = {}
_jwk_clients_lock = threading.Lock()
DISCOVERY_RETRY_SECONDS = 30
KEY_REFRESH_SECONDS = 30


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
    a half-configured deployment refuses bearer JWTs instead of guessing. The
    same goes for a plain http issuer without OIDC_JWKS_URL, since keys found
    through discovery are only trusted over https.
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
    jwks_url = _setting("OIDC_JWKS_URL")
    if not jwks_url and urlsplit(issuer).scheme != "https":
        logger.error(
            "MCP OIDC: keys are only discovered from an https OIDC_ISSUER; "
            "set OIDC_JWKS_URL to fetch them over plain http"
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

    return OIDCConfig(issuer, audience, provider, jwks_url, client_users)


def _jwks_url(config: OIDCConfig) -> str:
    """Return OIDC_JWKS_URL if set, else the jwks_uri from the issuer's discovery.

    An explicit OIDC_JWKS_URL may be plain http (an in-cluster address, say).
    Anything learned through discovery has to come over https: get_config
    already refuses an http issuer without OIDC_JWKS_URL, and the jwks_uri the
    document names must be https too.
    """
    if config.jwks_url:
        return config.jwks_url
    discovery = config.issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(discovery, timeout=5) as response:
        jwks_uri = json.load(response).get("jwks_uri")
    if not isinstance(jwks_uri, str) or urlsplit(jwks_uri).scheme != "https":
        raise exceptions.AuthenticationFailed(
            "OIDC discovery document has no https jwks_uri"
        )
    return jwks_uri


class _JWKClient(PyJWKClient):
    """A PyJWKClient that refreshes for an unknown ``kid`` at most every KEY_REFRESH_SECONDS.

    The stock client refetches the key set on every miss, so each request
    with a made-up ``kid`` would cost a round trip to the provider. A key the
    provider has just rotated in is still found, KEY_REFRESH_SECONDS late at
    worst.
    """

    _refresh_lock = threading.Lock()
    _refreshed_at = float("-inf")

    def get_signing_keys(self, refresh: bool = False) -> list[PyJWK]:
        if refresh:
            with self._refresh_lock:
                now = time.monotonic()
                refresh = now - self._refreshed_at >= KEY_REFRESH_SECONDS
                if refresh:
                    self._refreshed_at = now
        return super().get_signing_keys(refresh)


def _jwk_client(config: OIDCConfig) -> Any:
    """Return the cached JWKS client for this issuer, building it on first use.

    Building it may fetch the discovery document. Any failure there (issuer
    unreachable, timeout, a malformed document) becomes a clean 401, not an
    unhandled exception, and is remembered for DISCOVERY_RETRY_SECONDS so an
    IdP outage doesn't cost every request another blocking fetch. The build
    runs under a lock, so requests racing on a cold cache wait for one fetch
    instead of each starting their own.
    """
    key = config.jwks_url or config.issuer
    client = _jwk_clients.get(key)
    if client is not None:
        return client

    with _jwk_clients_lock:
        client = _jwk_clients.get(key)
        if client is not None:
            return client

        failed_at = _discovery_failures.get(key)
        if (
            failed_at is not None
            and time.monotonic() - failed_at < DISCOVERY_RETRY_SECONDS
        ):
            raise exceptions.AuthenticationFailed("OIDC provider unavailable")

        try:
            jwks_url = _jwks_url(config)
        except Exception as exc:
            _discovery_failures[key] = time.monotonic()
            logger.warning("MCP OIDC discovery for %s failed: %s", config.issuer, exc)
            if isinstance(exc, exceptions.AuthenticationFailed):
                raise
            raise exceptions.AuthenticationFailed("OIDC provider unavailable") from exc

        _discovery_failures.pop(key, None)
        client = _jwk_clients[key] = _JWKClient(
            jwks_url, cache_keys=True, lifespan=300, timeout=5
        )
        return client


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
