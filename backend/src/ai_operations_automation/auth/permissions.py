"""Small fixed permission boundary for implemented protected queries."""

from ai_operations_automation.auth.models import AuthenticatedHuman
from ai_operations_automation.intake.errors import IntakeError

SERVICE_REQUEST_READERS = {"OperationsAgent", "ManagerApprover", "Administrator"}
TERMINAL_DISPOSITION_ROLES = {"ManagerApprover", "Administrator"}


def require_service_request_permission(human: AuthenticatedHuman) -> AuthenticatedHuman:
    if human.role not in SERVICE_REQUEST_READERS:
        raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
    return human


def require_terminal_disposition_permission(
    human: AuthenticatedHuman,
) -> AuthenticatedHuman:
    if human.role not in TERMINAL_DISPOSITION_ROLES:
        raise IntakeError(403, "FORBIDDEN", "The requested operation is not permitted.")
    return human
