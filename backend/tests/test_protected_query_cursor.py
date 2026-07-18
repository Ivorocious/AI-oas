from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.protected_queries import cursors as cursor_module
from ai_operations_automation.protected_queries.cursors import decode_cursor, encode_cursor
from ai_operations_automation.protected_queries.models import RequestListItem

CURSOR_KEY = b"synthetic-query-cursor-key-for-tests-only-0001"
ORDERING = "created_at:desc,id:desc"
SCOPE = "human:OperationsAgent"


def test_cursor_is_opaque_round_trips_and_is_bound_to_its_filters() -> None:
    filters = {"queue": "StandardRequests", "priority": None, "status": "ReadyForAction"}
    stamp = datetime(2026, 7, 17, 12, tzinfo=UTC)
    cursor = encode_cursor(
        CURSOR_KEY,
        "service-requests",
        filters,
        stamp,
        "00000000-0000-0000-0000-000000000001",
        ordering=ORDERING,
        principal_scope=SCOPE,
    )

    assert "StandardRequests" not in cursor
    assert decode_cursor(
        CURSOR_KEY,
        cursor,
        "service-requests",
        filters,
        ordering=ORDERING,
        principal_scope=SCOPE,
    ) == (
        stamp,
        "00000000-0000-0000-0000-000000000001",
    )
    with pytest.raises(IntakeError) as failure:
        decode_cursor(
            CURSOR_KEY,
            cursor,
            "service-requests",
            {**filters, "status": "Completed"},
            ordering=ORDERING,
            principal_scope=SCOPE,
        )
    assert failure.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize("cursor", ["not-a-cursor", "", "A"])
def test_malformed_cursor_is_rejected_without_parser_detail(cursor: str) -> None:
    with pytest.raises(IntakeError) as failure:
        decode_cursor(
            CURSOR_KEY,
            cursor,
            "service-requests",
            {"queue": None, "priority": None, "status": None},
            ordering=ORDERING,
            principal_scope=SCOPE,
        )
    assert failure.value.status_code == 400
    assert failure.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize(
    ("kind", "ordering", "scope"),
    [
        ("audit-events", ORDERING, SCOPE),
        ("service-requests", "updated_at:desc,id:desc", SCOPE),
        ("service-requests", ORDERING, "human:Administrator"),
        ("service-requests", ORDERING, "workflow:test:other-service"),
    ],
)
def test_cursor_rejects_cross_projection_ordering_and_principal_scope(
    kind: str, ordering: str, scope: str
) -> None:
    filters = {"queue": None, "priority": None, "status": None}
    cursor = encode_cursor(
        CURSOR_KEY,
        "service-requests",
        filters,
        datetime(2026, 7, 17, 12, tzinfo=UTC),
        "00000000-0000-0000-0000-000000000001",
        ordering=ORDERING,
        principal_scope=SCOPE,
    )
    with pytest.raises(IntakeError) as failure:
        decode_cursor(
            CURSOR_KEY,
            cursor,
            kind,
            filters,
            ordering=ordering,
            principal_scope=scope,
        )
    assert failure.value.code == "INVALID_CURSOR"


def test_cursor_signing_material_fails_closed_only_when_cursor_work_is_required() -> None:
    filters = {"queue": None, "priority": None, "status": None}
    assert (
        decode_cursor(
            None,
            None,
            "service-requests",
            filters,
            ordering=ORDERING,
            principal_scope=SCOPE,
        )
        is None
    )
    with pytest.raises(IntakeError) as failure:
        encode_cursor(
            None,
            "service-requests",
            filters,
            datetime(2026, 7, 17, 12, tzinfo=UTC),
            "00000000-0000-0000-0000-000000000001",
            ordering=ORDERING,
            principal_scope=SCOPE,
        )
    assert failure.value.status_code == 503


def test_tampered_and_unknown_version_cursors_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters = {"queue": None, "priority": None, "status": None}
    stamp = datetime(2026, 7, 17, 12, tzinfo=UTC)
    cursor = encode_cursor(
        CURSOR_KEY,
        "service-requests",
        filters,
        stamp,
        "00000000-0000-0000-0000-000000000001",
        ordering=ORDERING,
        principal_scope=SCOPE,
    )
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    with pytest.raises(IntakeError):
        decode_cursor(
            CURSOR_KEY,
            tampered,
            "service-requests",
            filters,
            ordering=ORDERING,
            principal_scope=SCOPE,
        )

    monkeypatch.setattr(cursor_module, "_CURSOR_VERSION", 99)
    future = encode_cursor(
        CURSOR_KEY,
        "service-requests",
        filters,
        stamp,
        "00000000-0000-0000-0000-000000000001",
        ordering=ORDERING,
        principal_scope=SCOPE,
    )
    monkeypatch.setattr(cursor_module, "_CURSOR_VERSION", 1)
    with pytest.raises(IntakeError):
        decode_cursor(
            CURSOR_KEY,
            future,
            "service-requests",
            filters,
            ordering=ORDERING,
            principal_scope=SCOPE,
        )


def test_query_item_schema_rejects_unapproved_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RequestListItem.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "status": "ReadyForAction",
                "category": "Repair",
                "priority": "High",
                "current_queue": "PriorityRequests",
                "review_required": False,
                "created_at": "2026-07-17T12:00:00Z",
                "updated_at": "2026-07-17T12:00:00Z",
                "version": 1,
                "raw_provider_payload": {"secret": "must-not-serialize"},
            }
        )
