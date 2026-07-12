"""Focused normalization for accepted public-intake fields."""

import re

PHONE_SEPARATORS = re.compile(r"[\s().-]")
E164_PHONE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(value: str) -> str:
    """Normalize accepted separators into an unambiguous E.164 representation."""
    normalized = PHONE_SEPARATORS.sub("", value.strip())
    if not E164_PHONE.fullmatch(normalized):
        raise ValueError("phone must be an unambiguous international number")
    if len(normalized) > 32:
        raise ValueError("normalized phone exceeds maximum length")
    return normalized


def optional_trimmed(value: str | None) -> str | None:
    """Trim optional text and represent blanks as absent."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
