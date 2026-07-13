import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Request

from ai_operations_automation.auth.dependencies import authenticated_retry_authority
from ai_operations_automation.intake.errors import IntakeError


class FailOnStateAccess:
    def __getattr__(self, name):
        raise AssertionError(f"authority rejection must not access application state: {name}")


def authority_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/service-requests/example/commands/retry-ai",
            "raw_path": b"/api/v1/service-requests/example/commands/retry-ai",
            "query_string": b"",
            "headers": headers,
            "app": SimpleNamespace(state=FailOnStateAccess()),
        }
    )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [
            (b"authorization", b"Bearer human-token"),
            (b"x-service-id", b"workflow.test"),
        ],
        [
            (b"authorization", b"Bearer first"),
            (b"authorization", b"Bearer second"),
        ],
    ],
)
def test_retry_authority_rejects_absent_mixed_or_duplicate_authority_before_io(headers) -> None:
    with pytest.raises(IntakeError) as caught:
        asyncio.run(authenticated_retry_authority(authority_request(headers), uuid.uuid4()))
    assert (caught.value.status_code, caught.value.code) == (401, "AUTHENTICATION_REQUIRED")


@pytest.mark.parametrize(
    "authorization",
    [b"Basic opaque", b"Bearer", b"Bearer ", b"Bearer  leading", b"Bearer trailing "],
)
def test_retry_authority_rejects_malformed_human_family_before_verification(
    authorization: bytes,
) -> None:
    request = authority_request([(b"authorization", authorization)])
    with pytest.raises(IntakeError) as caught:
        asyncio.run(authenticated_retry_authority(request, uuid.uuid4()))
    assert (caught.value.status_code, caught.value.code) == (401, "AUTHENTICATION_REQUIRED")
