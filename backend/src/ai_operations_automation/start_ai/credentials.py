"""Opaque callback credential generation and one-way hashing."""

import base64
import hashlib
import secrets


def generate_callback_credential() -> str:
    """Return an unpadded URL-safe token carrying 256 random bits."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def callback_credential_hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()
