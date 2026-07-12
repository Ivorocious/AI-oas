"""Lazy, bounded JWKS-backed asymmetric Supabase token verification."""

import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import jwt


class AuthenticationFailure(Exception):
    pass


class KeyDiscoveryFailure(Exception):
    pass


JwksLoader = Callable[[], dict[str, Any]]


def url_jwks_loader(url: str) -> JwksLoader:
    def load() -> dict[str, Any]:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                value = json.load(response)
        except Exception as exc:
            raise KeyDiscoveryFailure from exc
        if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            raise KeyDiscoveryFailure
        return value

    return load


class SupabaseJwtVerifier:
    def __init__(self, issuer: str, audience: str, loader: JwksLoader, cache_seconds: int) -> None:
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.loader = loader
        self.cache_seconds = cache_seconds
        self._keys: dict[str, jwt.PyJWK] = {}
        self._loaded_at = 0.0

    def verify(self, token: str) -> str:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationFailure from exc
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise AuthenticationFailure
        kid = header["kid"]
        key = self._key(kid, refresh=False)
        if key is None:
            key = self._key(kid, refresh=True)
        if key is None:
            raise AuthenticationFailure
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationFailure from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationFailure
        return subject.strip()

    def _key(self, kid: str, *, refresh: bool) -> jwt.PyJWK | None:
        expired = time.monotonic() - self._loaded_at >= self.cache_seconds
        if refresh or not self._keys or expired:
            document = self.loader()
            if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
                raise KeyDiscoveryFailure
            try:
                self._keys = {
                    item["kid"]: jwt.PyJWK.from_dict(item)
                    for item in document["keys"]
                    if isinstance(item, dict) and isinstance(item.get("kid"), str)
                }
            except (KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
                raise KeyDiscoveryFailure from exc
            self._loaded_at = time.monotonic()
        return self._keys.get(kid)
