"""Read-only request/contact projection query."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.db.models.intake import Contact, ServiceRequest
from ai_operations_automation.service_requests.models import (
    ActiveReferences,
    ContactView,
    ServiceRequestResponse,
    ServiceRequestResult,
    ServiceRequestView,
)


def query_service_request(
    session_factory: sessionmaker[Session], request_id: uuid.UUID, correlation_id: uuid.UUID
) -> ServiceRequestResponse | None:
    with session_factory() as session:
        row = session.execute(
            select(ServiceRequest, Contact)
            .join(Contact, Contact.id == ServiceRequest.contact_id)
            .where(ServiceRequest.id == request_id)
        ).one_or_none()
    if row is None:
        return None
    request, contact = row
    return ServiceRequestResponse(
        correlation_id=correlation_id,
        result=ServiceRequestResult(
            service_request=ServiceRequestView(
                id=request.id,
                status=request.status,
                category=request.category,
                priority=request.priority,
                current_queue=request.current_queue,
                description=request.normalized_request_description,
                location_context=request.location_context,
                timing_preference=request.timing_preference,
                review_required=request.review_required,
                review_reason_codes=request.review_reason_codes,
                created_at=request.created_at,
                updated_at=request.updated_at,
                version=request.version,
            ),
            contact=ContactView(
                id=contact.id,
                display_name=contact.display_label,
                email=contact.normalized_email,
                phone=contact.normalized_phone,
                preferred_channel=contact.preferred_channel,
                version=contact.version,
            ),
            active_references=ActiveReferences(
                current_interpretation_id=request.current_interpretation_id,
                current_routing_decision_id=request.current_routing_decision_id,
            ),
        ),
    )
