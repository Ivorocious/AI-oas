"""PII-minimized normalized evidence construction for deterministic decisions."""

import hashlib
import re
import unicodedata

from ai_operations_automation.db.models.intake import Contact, ServiceRequest
from ai_operations_automation.deterministic_decision.models import NormalizedDecisionFacts
from ai_operations_automation.triage.models import AuthoritativeDecisionFacts

TOKEN = re.compile(r"[a-z0-9]+")


def normalized_digest(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def description_evidence(value: str) -> tuple[str, tuple[str, ...]]:
    normalized = " ".join(TOKEN.findall(unicodedata.normalize("NFKC", value).casefold()))
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    tokens = tuple(
        sorted({hashlib.sha256(token.encode("utf-8")).hexdigest() for token in normalized.split()})
    )
    return fingerprint, tokens


def build_normalized_facts(
    service_request: ServiceRequest,
    contact: Contact,
    supplied: AuthoritativeDecisionFacts,
) -> NormalizedDecisionFacts:
    """Bind trusted normalized facts to canonical request/contact evidence."""
    fingerprint, token_digests = description_evidence(
        service_request.normalized_request_description
    )
    return NormalizedDecisionFacts(
        source_request_id=service_request.id,
        explicit_category=supplied.explicit_category,
        contact_method_present=bool(contact.normalized_email or contact.normalized_phone),
        timing_preference_present=bool(
            service_request.timing_preference
            or supplied.requested_deadline
            or supplied.timing_is_flexible
        ),
        timing_is_flexible=supplied.timing_is_flexible,
        requested_deadline=supplied.requested_deadline,
        requested_service_date=supplied.requested_service_date,
        service_mode=supplied.service_mode,
        location_or_service_context_present=bool(
            service_request.location_context or supplied.service_mode.value == "Remote"
        ),
        access_constraints_known=supplied.access_constraints_known,
        consultation_topic_present=supplied.consultation_topic_present,
        desired_outcome_present=supplied.desired_outcome_present,
        installation_target_present=supplied.installation_target_present,
        installation_scope_present=supplied.installation_scope_present,
        repair_symptoms_present=supplied.repair_symptoms_present,
        repair_asset_context_present=supplied.repair_asset_context_present,
        maintenance_asset_context_present=supplied.maintenance_asset_context_present,
        inspection_subject_present=supplied.inspection_subject_present,
        inspection_purpose_present=supplied.inspection_purpose_present,
        custom_scope_present=supplied.custom_scope_present,
        safety_or_continuity_concern=supplied.safety_or_continuity_concern,
        service_interruption=supplied.service_interruption,
        damage_or_deterioration=supplied.damage_or_deterioration,
        material_impact=supplied.material_impact,
        contact_id=contact.id,
        normalized_email_digest=(
            normalized_digest(contact.normalized_email)
            if contact.normalized_email is not None
            else None
        ),
        normalized_phone_digest=(
            normalized_digest(contact.normalized_phone)
            if contact.normalized_phone is not None
            else None
        ),
        description_fingerprint=fingerprint,
        description_token_digests=token_digests,
        location_or_service_context_digest=(
            normalized_digest(service_request.location_context)
            if service_request.location_context is not None
            else None
        ),
    )
