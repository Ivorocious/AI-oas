"""Deterministic WorkflowService request-target and signing canonicalization."""

import hashlib
import re
from urllib.parse import quote

from ai_operations_automation.intake.errors import IntakeError

_PERCENT_ESCAPE = re.compile(rb"%[0-9A-Fa-f]{2}")


def _reject_malformed_percent(value: bytes) -> None:
    index = 0
    while index < len(value):
        if value[index : index + 1] == b"%":
            if _PERCENT_ESCAPE.fullmatch(value[index : index + 3]) is None:
                raise IntakeError(
                    401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed."
                )
            index += 3
        else:
            index += 1


def canonical_path(raw_path: bytes) -> str:
    _reject_malformed_percent(raw_path)
    try:
        path = raw_path.decode("ascii")
    except UnicodeDecodeError as exc:
        raise IntakeError(
            401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed."
        ) from exc
    return re.sub(r"%[0-9A-Fa-f]{2}", lambda match: match.group(0).upper(), path)


def extract_request_target(scope: dict) -> tuple[bytes, bytes]:
    raw_path = scope.get("raw_path")
    if not isinstance(raw_path, bytes) or not raw_path or not raw_path.startswith(b"/"):
        raise IntakeError(401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed.")
    if any(byte < 0x21 or byte > 0x7E for byte in raw_path) or b"?" in raw_path or b"#" in raw_path:
        raise IntakeError(401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed.")
    canonical_path(raw_path)
    raw_query = scope.get("query_string", b"")
    if not isinstance(raw_query, bytes):
        raise IntakeError(401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed.")
    return raw_path, raw_query


def _decode_query_component(value: bytes) -> str:
    _reject_malformed_percent(value)
    output = bytearray()
    index = 0
    while index < len(value):
        if value[index : index + 1] == b"%":
            output.append(int(value[index + 1 : index + 3], 16))
            index += 3
        elif value[index : index + 1] == b"+":
            output.append(0x20)
            index += 1
        else:
            output.append(value[index])
            index += 1
    try:
        return bytes(output).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntakeError(
            401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed."
        ) from exc


def canonical_query(raw_query: bytes) -> str:
    if not raw_query:
        return ""
    pairs: list[tuple[str, str]] = []
    for field in raw_query.split(b"&"):
        key, separator, value = field.partition(b"=")
        if not separator:
            value = b""
        pairs.append((_decode_query_component(key), _decode_query_component(value)))
    pairs.sort()
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}" for key, value in pairs
    )


def canonical_path_and_query(raw_path: bytes, raw_query: bytes) -> str:
    path = canonical_path(raw_path)
    query = canonical_query(raw_query)
    return f"{path}?{query}" if query else path


def canonical_signing_bytes(
    method: str,
    raw_path: bytes,
    raw_query: bytes,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    target = canonical_path_and_query(raw_path, raw_query)
    return f"{method.upper()}\n{target}\n{timestamp}\n{nonce}\n{body_digest}".encode()
