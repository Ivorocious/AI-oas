import math
import uuid

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.requests import Request

from ai_operations_automation.command_idempotency.canonicalization import (
    canonical_command_bytes,
    canonical_command_hash,
)
from ai_operations_automation.command_idempotency.keys import (
    command_key_digest,
    resolve_command_idempotency_key,
    validate_command_idempotency_key,
)
from ai_operations_automation.command_idempotency.models import CommandIdempotencyScope
from ai_operations_automation.command_idempotency.snapshots import (
    FORBIDDEN_KEYS,
    MAX_SAFE_SNAPSHOT_BYTES,
    validate_safe_snapshot,
)
from ai_operations_automation.intake.errors import IntakeError


class SyntheticCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int
    label: str
    note: str | None = None


def request_with_keys(*values: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/__tests__/commands",
            "headers": [(b"idempotency-key", value.encode("latin-1")) for value in values],
        }
    )


@pytest.mark.parametrize("value", ["a" * 8, "b" * 128])
def test_valid_command_key_boundaries(value) -> None:
    assert validate_command_idempotency_key(value) == value
    assert resolve_command_idempotency_key(request_with_keys(value)) == value


@pytest.mark.parametrize(
    "value",
    ["", "a" * 7, "a" * 129, "contains space", "tab\there", "control\x01", "unicode-é"],
)
def test_invalid_command_keys_are_safe_400(value) -> None:
    with pytest.raises(IntakeError) as captured:
        validate_command_idempotency_key(value)
    assert captured.value.status_code == 400
    assert captured.value.code == "MISSING_IDEMPOTENCY_KEY"


@pytest.mark.parametrize("values", [(), ("valid-key-1", "valid-key-2")])
def test_missing_or_duplicate_command_key_is_rejected(values) -> None:
    with pytest.raises(IntakeError) as captured:
        resolve_command_idempotency_key(request_with_keys(*values))
    assert captured.value.code == "MISSING_IDEMPOTENCY_KEY"


def test_command_key_digest_is_deterministic_and_raw_key_is_not_a_model_field() -> None:
    raw = "never-store-command-key"
    assert command_key_digest(raw) == command_key_digest(raw)
    assert len(command_key_digest(raw)) == 64
    assert all("key" not in name for name in CommandIdempotencyScope.model_fields)
    assert raw not in repr(
        CommandIdempotencyScope(
            actor_class="MachineService",
            actor_id=uuid.uuid4(),
            command_intent="StartAiInterpretation",
            route_template="/api/v1/service-requests/{request_id}/commands/start-ai-interpretation",
            target_type="ServiceRequest",
            target_id=uuid.uuid4(),
        )
    )


def test_canonical_command_binding_is_stable_complete_and_value_sensitive() -> None:
    left = SyntheticCommand.model_validate({"label": "same", "expected_version": 2, "note": None})
    right = SyntheticCommand.model_validate_json(
        '{ "note": null, "expected_version": 2, "label": "same" }'
    )
    assert canonical_command_bytes(left) == canonical_command_bytes(right)
    assert canonical_command_hash(left) == canonical_command_hash(right)
    assert b'"note":null' in canonical_command_bytes(left)
    assert canonical_command_hash(left) != canonical_command_hash(
        left.model_copy(update={"label": "changed"})
    )
    assert canonical_command_hash(left) != canonical_command_hash(
        left.model_copy(update={"expected_version": 3})
    )


def test_nonfinite_command_data_is_rejected() -> None:
    class NumericCommand(BaseModel):
        value: float

    with pytest.raises(ValueError):
        canonical_command_bytes(NumericCommand(value=math.nan))


def test_safe_snapshot_accepts_result_and_safe_credential_identifiers() -> None:
    snapshot = {
        "result": "accepted",
        "nested": {"callback_credential_id": str(uuid.uuid4()), "callback_credential_version": 1},
    }
    assert validate_safe_snapshot(snapshot) == snapshot


def test_safe_tuple_is_normalized_to_json_list() -> None:
    credential_id = str(uuid.uuid4())
    normalized = validate_safe_snapshot(
        {"items": ("one", {"callback_credential_id": credential_id}, (True, None))}
    )
    assert normalized == {"items": ["one", {"callback_credential_id": credential_id}, [True, None]]}
    assert isinstance(normalized["items"], list)
    assert isinstance(normalized["items"][2], list)


@pytest.mark.parametrize("key", ["secret", "callback_credential_hash"])
def test_tuple_cannot_bypass_forbidden_key_inspection(key) -> None:
    with pytest.raises(ValueError):
        validate_safe_snapshot({"items": ({key: "forbidden"},)})


def test_deep_mixed_sequences_cannot_bypass_case_insensitive_inspection() -> None:
    with pytest.raises(ValueError):
        validate_safe_snapshot(
            {"outer": ([{"safe": (({"CaLlBaCk_CrEdEnTiAl_HaSh": "forbidden"},),)}],)}
        )


def test_safe_snapshot_size_and_nonfinite_limits() -> None:
    with pytest.raises(ValueError):
        validate_safe_snapshot({"value": "x" * MAX_SAFE_SNAPSHOT_BYTES})
    with pytest.raises(ValueError):
        validate_safe_snapshot({"value": math.inf})


@pytest.mark.parametrize("key", sorted(FORBIDDEN_KEYS))
def test_forbidden_snapshot_keys_are_rejected_case_insensitively(key) -> None:
    with pytest.raises(ValueError):
        validate_safe_snapshot({"nested": [{key.upper(): "redacted"}]})


def valid_scope(**changes) -> CommandIdempotencyScope:
    values = {
        "actor_class": "MachineService",
        "actor_id": uuid.uuid4(),
        "command_intent": "StartAiInterpretation",
        "route_template": (
            "/api/v1/service-requests/{request_id}/commands/start-ai-interpretation"
        ),
        "target_type": "ServiceRequest",
        "target_id": uuid.uuid4(),
        **changes,
    }
    return CommandIdempotencyScope(**values)


def test_scope_is_immutable() -> None:
    scope = valid_scope()
    with pytest.raises(ValidationError):
        scope.command_intent = "RetryAi"


@pytest.mark.parametrize("actor_class", ["Customer", "WorkflowService", ""])
def test_invalid_actor_class_is_rejected(actor_class) -> None:
    with pytest.raises(ValidationError):
        valid_scope(actor_class=actor_class)


@pytest.mark.parametrize("field", ["command_intent", "target_type"])
@pytest.mark.parametrize("value", ["", " starts-wrong", "1StartsWrong", "unsafe/value"])
def test_invalid_safe_scope_identifiers_are_rejected(field, value) -> None:
    with pytest.raises(ValidationError):
        valid_scope(**{field: value})


@pytest.mark.parametrize(
    "value",
    ["", "relative", "/has space", "/has?query", "/has#fragment", "/control\x01"],
)
def test_invalid_route_template_is_rejected(value) -> None:
    with pytest.raises(ValidationError):
        valid_scope(route_template=value)
