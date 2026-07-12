import json
import uuid

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from ai_operations_automation.api.intake import (
    _correlation_id,
    _idempotency_key,
    _validate_content_type,
)
from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.intake.canonicalization import (
    canonical_json,
    canonical_payload_hash,
    idempotency_key_digest,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.intake.models import IntakeRequest
from ai_operations_automation.intake.normalization import normalize_phone


def request_with_headers(**headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [
                (key.lower().replace("_", "-").encode(), value.encode())
                for key, value in headers.items()
            ],
        }
    )


def payload(**service_overrides):
    service = {
        "description": "The air-conditioning unit is leaking.",
        "location_context": "Second-floor office",
        "timing_preference": "Weekday morning",
    }
    service.update(service_overrides)
    return {
        "schema_version": "1.0",
        "contact": {
            "display_name": " Jane Doe ",
            "email": " JANE@EXAMPLE.COM ",
            "phone": "+63 (917) 123-4567",
            "preferred_channel": "Email",
        },
        "service_request": service,
    }


@pytest.mark.parametrize("key", ["short", " leading-value", "trailing-value ", "bad key value"])
def test_unusable_idempotency_keys_are_rejected(key: str) -> None:
    with pytest.raises(IntakeError) as exc_info:
        _idempotency_key(request_with_headers(idempotency_key=key))
    assert exc_info.value.code == "MISSING_IDEMPOTENCY_KEY"


def test_idempotency_digest_is_stable_and_lowercase_hex() -> None:
    digest = idempotency_key_digest("opaque-key-123")
    assert digest == idempotency_key_digest("opaque-key-123")
    assert len(digest) == 64
    assert digest == digest.lower()


def test_correlation_id_is_generated_or_accepted() -> None:
    generated = _correlation_id(request_with_headers())
    supplied = uuid.uuid4()
    assert isinstance(generated, uuid.UUID)
    assert _correlation_id(request_with_headers(x_correlation_id=str(supplied))) == supplied


def test_invalid_correlation_id_is_rejected() -> None:
    with pytest.raises(IntakeError) as exc_info:
        _correlation_id(request_with_headers(x_correlation_id="not-a-uuid"))
    assert exc_info.value.code == "INVALID_TRANSPORT_IDENTIFIER"


def test_contact_and_phone_normalization() -> None:
    model = IntakeRequest.model_validate(payload())
    assert model.contact.display_name == "Jane Doe"
    assert str(model.contact.email) == "jane@example.com"
    assert model.contact.phone == "+639171234567"
    assert normalize_phone("+63-917-123-4567") == "+639171234567"


def test_ambiguous_phone_and_preferred_channel_are_rejected() -> None:
    data = payload()
    data["contact"] = {"display_name": "Jane", "phone": "09171234567"}
    with pytest.raises(ValidationError):
        IntakeRequest.model_validate(data)

    data["contact"] = {
        "display_name": "Jane",
        "phone": "+639171234567",
        "preferred_channel": "Email",
    }
    with pytest.raises(ValidationError):
        IntakeRequest.model_validate(data)


def test_schema_bounds_and_unknown_fields_are_rejected() -> None:
    data = payload(description="tiny")
    data["unexpected"] = True
    with pytest.raises(ValidationError) as exc_info:
        IntakeRequest.model_validate(data)
    assert {error["type"] for error in exc_info.value.errors()} >= {
        "string_too_short",
        "extra_forbidden",
    }


def test_optional_blank_context_becomes_null() -> None:
    model = IntakeRequest.model_validate(payload(location_context="  ", timing_preference=""))
    assert model.service_request.location_context is None
    assert model.service_request.timing_preference is None


def test_canonical_json_is_compact_sorted_and_unicode_preserving() -> None:
    model = IntakeRequest.model_validate(payload(description="Inspect the café cooling system."))
    encoded = canonical_json(model)
    assert b", " not in encoded
    assert b": " not in encoded
    assert "café" in encoded.decode()
    assert json.loads(encoded)["schema_version"] == "1.0"


def test_equivalent_normalized_payloads_hash_equally() -> None:
    first = IntakeRequest.model_validate(payload())
    second_data = payload()
    second_data["contact"]["email"] = "jane@example.com"
    second_data["contact"]["phone"] = "+63-917-123-4567"
    second_data["service_request"]["description"] = "  The air-conditioning unit is leaking.  "
    second = IntakeRequest.model_validate(second_data)
    assert canonical_payload_hash(first) == canonical_payload_hash(second)


@pytest.mark.parametrize(
    "change",
    [
        {"description": "The air-conditioning unit is making a loud noise."},
        {"location_context": "Ground-floor office"},
        {"timing_preference": "Weekend afternoon"},
    ],
)
def test_meaningful_changes_produce_different_hashes(change: dict[str, str]) -> None:
    first = IntakeRequest.model_validate(payload())
    second = IntakeRequest.model_validate(payload(**change))
    assert canonical_payload_hash(first) != canonical_payload_hash(second)


def test_error_envelope_contains_no_rejected_value() -> None:
    correlation_id = uuid.uuid4()
    body = IntakeError(
        422,
        "INTAKE_VALIDATION_FAILED",
        "Invalid intake.",
        details=[{"field": "contact.email", "issue_code": "INVALID_FIELD"}],
    ).response(correlation_id)
    serialized = json.dumps(body)
    assert "jane@example.com" not in serialized
    assert body["error"]["code"] == "INTAKE_VALIDATION_FAILED"


def test_openapi_documents_intake_schema_and_statuses() -> None:
    schema = create_app(Settings(_env_file=None)).openapi()
    operation = schema["paths"]["/api/v1/intake/service-requests"]["post"]
    assert "requestBody" in operation
    assert {"200", "201", "400", "409", "415", "422", "500", "503"} <= set(operation["responses"])
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]

    def assert_refs_resolve(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/"):
                target: object = schema
                for part in reference[2:].split("/"):
                    target = target[part]  # type: ignore[index]
                assert target is not None
            for item in value.values():
                assert_refs_resolve(item)
        elif isinstance(value, list):
            for item in value:
                assert_refs_resolve(item)

    assert_refs_resolve(request_schema)
    assert {"schema_version", "contact", "service_request"} <= set(request_schema["properties"])
    assert "display_name" in request_schema["properties"]["contact"]["properties"]
    assert "description" in request_schema["properties"]["service_request"]["properties"]


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "application/json; charset=utf-8", 'application/json; charset="UTF-8"'],
)
def test_json_content_type_variants_are_accepted(content_type: str) -> None:
    _validate_content_type(request_with_headers(content_type=content_type))


@pytest.mark.parametrize("content_type", ["text/plain", "application/json; charset=iso-8859-1"])
def test_non_json_or_non_utf8_content_type_is_rejected(content_type: str) -> None:
    with pytest.raises(IntakeError) as exc_info:
        _validate_content_type(request_with_headers(content_type=content_type))
    assert exc_info.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_overlong_email_is_rejected_by_closed_schema() -> None:
    data = payload()
    data["contact"]["email"] = f"{'a' * 310}@example.com"
    with pytest.raises(ValidationError):
        IntakeRequest.model_validate(data)
