"""Focused canonical hashes for Start AI interpretation."""

import hashlib
import json
from typing import Any

from ai_operations_automation.config import Settings
from ai_operations_automation.db.models.intake import ServiceRequest


def _canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ai_input_material(request: ServiceRequest) -> dict[str, Any]:
    return {
        "input_schema_version": "1.0",
        "location_context": request.location_context,
        "request_description": request.normalized_request_description,
        "timing_preference": request.timing_preference,
    }


def ai_input_hash(request: ServiceRequest) -> str:
    return _canonical_hash(ai_input_material(request))


def ai_configuration_material(settings: Settings) -> dict[str, str]:
    return {
        "adapter_name": settings.ai_adapter_name,
        "adapter_version": settings.ai_adapter_version,
        "model_name": settings.ai_model_name,
        "prompt_version": settings.ai_interpretation_prompt_version,
        "provider_name": settings.ai_provider_name,
        "result_schema_version": settings.ai_interpretation_result_schema_version,
    }


def ai_configuration_hash(settings: Settings) -> str:
    return _canonical_hash(ai_configuration_material(settings))
