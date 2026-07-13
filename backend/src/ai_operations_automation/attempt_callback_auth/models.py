"""Safe immutable authority context bound to one database transaction."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session, SessionTransaction


@dataclass(frozen=True, slots=True)
class VerifiedAttemptCallbackContext:
    machine_identity_id: uuid.UUID
    stable_service_id: str
    workflow_environment: str
    integration_attempt_id: uuid.UUID
    integration_attempt_version: int
    logical_operation_id: uuid.UUID
    service_request_id: uuid.UUID
    operation_kind: Literal["AIInterpretation"]
    callback_credential_id: uuid.UUID
    callback_credential_version: int
    callback_credential_expires_at: datetime
    _session: Session = field(repr=False, compare=False)
    _transaction: SessionTransaction = field(repr=False, compare=False)

    def assert_transaction_bound(self, session: Session) -> None:
        """Reject use outside the exact active transaction that established authority."""
        transaction = session.get_transaction()
        if (
            session is not self._session
            or transaction is not self._transaction
            or not self._transaction.is_active
        ):
            raise RuntimeError("callback authority is not bound to this active transaction")
