"""WorkflowService HMAC verification with committed nonce replay protection."""

import hashlib
import hmac
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ai_operations_automation.config import Settings
from ai_operations_automation.db.models.machine import (
    MachineCredentialVersion,
    MachineIdentity,
    MachineRequestNonce,
)
from ai_operations_automation.intake.errors import IntakeError
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.machine_auth.secrets import (
    MachineSecretResolver,
    MachineSecretUnavailable,
)

Clock = Callable[[], datetime]
_SERVICE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_NONCE = re.compile(r"^[!-~]{16,128}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


def machine_authentication_failure() -> IntakeError:
    return IntakeError(401, "MACHINE_AUTHENTICATION_FAILED", "Machine authentication failed.")


def validate_timestamp(value: str, now: datetime, skew_seconds: int) -> datetime:
    if not value.isascii() or not value.isdigit():
        raise machine_authentication_failure()
    try:
        signed_at = datetime.fromtimestamp(int(value), UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise machine_authentication_failure() from exc
    if abs((signed_at - now).total_seconds()) > skew_seconds:
        raise machine_authentication_failure()
    return signed_at


def validate_nonce(value: str) -> str:
    if _NONCE.fullmatch(value) is None:
        raise machine_authentication_failure()
    return value


def validate_signature(value: str) -> str:
    if _SIGNATURE.fullmatch(value) is None:
        raise machine_authentication_failure()
    return value


def calculate_signature(secret: bytes, signing_bytes: bytes) -> str:
    return hmac.new(secret, signing_bytes, hashlib.sha256).hexdigest()


def signatures_match(expected: str, supplied: str) -> bool:
    return hmac.compare_digest(expected, supplied)


class WorkflowServiceAuthenticator:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        secret_resolver: MachineSecretResolver,
        clock: Clock,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.secret_resolver = secret_resolver
        self.clock = clock

    async def authenticate(self, request: Request) -> AuthenticatedWorkflowService:
        headers = {}
        for name in (
            "X-Service-ID",
            "X-Service-Timestamp",
            "X-Service-Nonce",
            "X-Service-Signature",
        ):
            values = request.headers.getlist(name)
            if len(values) != 1 or not values[0]:
                raise machine_authentication_failure()
            headers[name] = values[0]
        service_id = headers["X-Service-ID"]
        if _SERVICE_ID.fullmatch(service_id) is None:
            raise machine_authentication_failure()
        now = self.clock().astimezone(UTC)
        signed_at = validate_timestamp(
            headers["X-Service-Timestamp"], now, self.settings.machine_clock_skew_seconds
        )
        nonce = validate_nonce(headers["X-Service-Nonce"])
        supplied_signature = validate_signature(headers["X-Service-Signature"])
        body = await request.body()
        signing_bytes = canonical_signing_bytes(
            request.method,
            request.scope.get("raw_path", request.url.path.encode("ascii")),
            request.scope.get("query_string", b""),
            headers["X-Service-Timestamp"],
            nonce,
            body,
        )
        try:
            with self.session_factory() as session:
                identity = session.scalar(
                    select(MachineIdentity).where(
                        MachineIdentity.environment == self.settings.app_environment,
                        MachineIdentity.stable_service_id == service_id,
                        MachineIdentity.service_type == "WorkflowService",
                        MachineIdentity.status == "Active",
                    )
                )
                if identity is None:
                    raise machine_authentication_failure()
                credentials = session.scalars(
                    select(MachineCredentialVersion).where(
                        MachineCredentialVersion.machine_identity_id == identity.id,
                        or_(
                            MachineCredentialVersion.status == "Current",
                            (
                                (MachineCredentialVersion.status == "Previous")
                                & (MachineCredentialVersion.previous_verification_until >= now)
                            ),
                        ),
                    )
                ).all()
        except IntakeError:
            raise
        except SQLAlchemyError as exc:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ) from exc
        matches: list[MachineCredentialVersion] = []
        for credential in credentials:
            try:
                secret = self.secret_resolver.resolve(credential.external_secret_reference)
            except MachineSecretUnavailable as exc:
                raise IntakeError(
                    503,
                    "DEPENDENCY_UNAVAILABLE",
                    "A required dependency is unavailable.",
                    True,
                ) from exc
            expected = calculate_signature(secret, signing_bytes)
            if signatures_match(expected, supplied_signature):
                matches.append(credential)
        if len(matches) != 1:
            raise machine_authentication_failure()
        credential = matches[0]
        nonce_digest = hashlib.sha256(nonce.encode()).hexdigest()
        try:
            with self.session_factory.begin() as session:
                session.add(
                    MachineRequestNonce(
                        id=uuid.uuid4(),
                        machine_identity_id=identity.id,
                        machine_credential_version_id=credential.id,
                        environment=self.settings.app_environment,
                        verified_credential_version=credential.credential_version,
                        nonce_digest=nonce_digest,
                        signed_at=signed_at,
                        expires_at=now
                        + timedelta(seconds=self.settings.machine_nonce_retention_seconds),
                    )
                )
        except IntegrityError as exc:
            raise machine_authentication_failure() from exc
        except SQLAlchemyError as exc:
            raise IntakeError(
                503, "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable.", True
            ) from exc
        return AuthenticatedWorkflowService(
            machine_identity_id=identity.id,
            stable_service_id=identity.stable_service_id,
            environment=identity.environment,
            service_type="WorkflowService",
            credential_id=credential.id,
            credential_version=credential.credential_version,
        )
