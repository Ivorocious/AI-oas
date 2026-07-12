import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from ai_operations_automation.app import create_app
from ai_operations_automation.config import Settings
from ai_operations_automation.db import Base, create_database_engine, create_session_factory
from ai_operations_automation.machine_auth.authenticator import calculate_signature
from ai_operations_automation.machine_auth.canonicalization import canonical_signing_bytes
from ai_operations_automation.machine_auth.dependencies import authenticated_workflow_service
from ai_operations_automation.machine_auth.models import AuthenticatedWorkflowService
from ai_operations_automation.machine_auth.secrets import MachineSecretUnavailable
from alembic import command

pytestmark = pytest.mark.integration
BACKEND_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
SECRET_CURRENT = b"synthetic-current-machine-key"
SECRET_PREVIOUS = b"synthetic-previous-machine-key"


class Resolver:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def resolve(self, reference: str) -> bytes:
        self.calls.append(reference)
        try:
            return self.values[reference]
        except KeyError as exc:
            raise MachineSecretUnavailable from exc


@pytest.fixture(scope="module")
def engine() -> Engine:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    value = create_database_engine(Settings(_env_file=None).database_url)
    yield value
    value.dispose()


@pytest.fixture
def app_client(engine: Engine):
    tables = ", ".join(f'"{name}"' for name in Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    app = create_app(
        Settings(app_environment="test", _env_file=None),
        create_session_factory(engine),
        machine_secret_resolver=Resolver(
            {"test/current": SECRET_CURRENT, "test/previous": SECRET_PREVIOUS}
        ),
        machine_clock=lambda: NOW,
    )

    @app.post("/__tests__/workflow-auth", include_in_schema=False)
    async def test_route(
        request: Request,
        machine: AuthenticatedWorkflowService = Depends(authenticated_workflow_service),
    ):
        return {"service_id": machine.stable_service_id, "body": (await request.body()).decode()}

    @app.post("/__tests__/workflow-auth-fails", include_in_schema=False)
    async def failing_route(
        _machine: AuthenticatedWorkflowService = Depends(authenticated_workflow_service),
    ):
        raise RuntimeError("forced downstream failure")

    @app.post("/__tests__/workflow-auth/{tail}", include_in_schema=False)
    async def tampered_path_route(
        tail: str,
        _machine: AuthenticatedWorkflowService = Depends(authenticated_workflow_service),
    ):
        return {"tail": tail}

    return app, TestClient(app), engine


def identity_values(**overrides):
    values = {
        "id": uuid.uuid4(),
        "service_type": "WorkflowService",
        "environment": "test",
        "stable_service_id": "workflow.test",
        "display_label": "Synthetic workflow service",
        "status": "Active",
    }
    values.update(overrides)
    return values


def credential_values(identity_id, **overrides):
    values = {
        "id": uuid.uuid4(),
        "machine_identity_id": identity_id,
        "credential_version": 1,
        "external_secret_reference": "test/current",
        "status": "Current",
        "activated_at": NOW - timedelta(days=1),
    }
    values.update(overrides)
    return values


def nonce_values(identity_id, credential_id, **overrides):
    values = {
        "id": uuid.uuid4(),
        "machine_identity_id": identity_id,
        "machine_credential_version_id": credential_id,
        "environment": "test",
        "verified_credential_version": 1,
        "nonce_digest": "a" * 64,
        "signed_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    values.update(overrides)
    return values


def insert_identity(engine, **overrides):
    values = identity_values(**overrides)
    with engine.begin() as connection:
        connection.execute(insert(Base.metadata.tables["machine_identities"]).values(**values))
    return values["id"]


def insert_credential(engine, identity_id, **overrides):
    values = credential_values(identity_id, **overrides)
    with engine.begin() as connection:
        connection.execute(
            insert(Base.metadata.tables["machine_credential_versions"]).values(**values)
        )
    return values["id"]


def assert_rejected(engine, statement):
    with pytest.raises(SQLAlchemyError):
        with engine.begin() as connection:
            connection.execute(statement)


def assert_constraint(engine, statement, expected):
    with pytest.raises(IntegrityError) as captured:
        with engine.begin() as connection:
            connection.execute(statement)
    assert captured.value.orig.diag.constraint_name == expected


def seed_current(engine, **identity_overrides):
    identity_id = insert_identity(engine, **identity_overrides)
    credential_id = insert_credential(engine, identity_id)
    return identity_id, credential_id


def signed_headers(
    secret=SECRET_CURRENT,
    *,
    path=b"/__tests__/workflow-auth",
    query=b"",
    body=b"payload",
    nonce="nonce-0123456789abcdef",
    timestamp=None,
    service_id="workflow.test",
):
    timestamp = timestamp or str(int(NOW.timestamp()))
    canonical = canonical_signing_bytes("POST", path, query, timestamp, nonce, body)
    return {
        "X-Service-ID": service_id,
        "X-Service-Timestamp": timestamp,
        "X-Service-Nonce": nonce,
        "X-Service-Signature": calculate_signature(secret, canonical),
    }


def test_machine_identity_constraints(app_client) -> None:
    _, _, engine = app_client
    identities = Base.metadata.tables["machine_identities"]
    insert_identity(engine)
    invalid = [
        identity_values(stable_service_id="workflow.test"),
        identity_values(service_type="Unknown"),
        identity_values(environment=" "),
        identity_values(stable_service_id="bad service"),
        identity_values(display_label=""),
        identity_values(version=0),
        identity_values(disabled_at=NOW),
        identity_values(status="Disabled"),
    ]
    for values in invalid:
        assert_rejected(engine, insert(identities).values(**values))


def test_machine_credential_constraints_and_restrictive_delete(app_client) -> None:
    _, _, engine = app_client
    identity_id = insert_identity(engine)
    credentials = Base.metadata.tables["machine_credential_versions"]
    identities = Base.metadata.tables["machine_identities"]
    insert_credential(engine, identity_id)
    invalid = [
        credential_values(identity_id, external_secret_reference="test/other"),
        credential_values(identity_id, credential_version=2),
        credential_values(identity_id, credential_version=0, external_secret_reference="test/zero"),
        credential_values(identity_id, external_secret_reference=" ", credential_version=2),
        credential_values(
            identity_id, status="Unknown", credential_version=2, external_secret_reference="x"
        ),
        credential_values(
            identity_id,
            status="Previous",
            credential_version=2,
            external_secret_reference="test/previous",
        ),
    ]
    for values in invalid:
        assert_rejected(engine, insert(credentials).values(**values))
    assert_rejected(engine, delete(identities).where(identities.c.id == identity_id))


def test_previous_uniqueness_and_state_consistency(app_client) -> None:
    _, _, engine = app_client
    identity_id = insert_identity(engine)
    credentials = Base.metadata.tables["machine_credential_versions"]
    insert_credential(
        engine,
        identity_id,
        credential_version=1,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW + timedelta(minutes=5),
    )
    assert_rejected(
        engine,
        insert(credentials).values(
            **credential_values(
                identity_id,
                credential_version=2,
                external_secret_reference="test/other",
                status="Previous",
                previous_verification_until=NOW + timedelta(minutes=10),
            )
        ),
    )


def test_machine_credential_uniqueness_constraints_are_isolated(app_client) -> None:
    _, _, engine = app_client
    credentials = Base.metadata.tables["machine_credential_versions"]
    first_identity = insert_identity(engine)
    second_identity = insert_identity(
        engine, stable_service_id="workflow.second", display_label="Second workflow"
    )
    insert_credential(
        engine,
        first_identity,
        status="Retired",
        retired_at=NOW,
    )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                first_identity,
                external_secret_reference="test/different",
                status="Revoked",
                revoked_at=NOW,
            )
        ),
        "uq_machine_credential_versions_identity_version",
    )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                second_identity,
                credential_version=2,
                external_secret_reference="test/current",
                status="Retired",
                retired_at=NOW,
            )
        ),
        "uq_machine_credential_versions_external_reference",
    )
    insert_credential(
        engine,
        first_identity,
        credential_version=2,
        external_secret_reference="test/current-2",
    )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                first_identity,
                credential_version=3,
                external_secret_reference="test/current-3",
            )
        ),
        "uq_machine_credential_versions_one_current",
    )
    insert_credential(
        engine,
        first_identity,
        credential_version=4,
        external_secret_reference="test/previous-4",
        status="Previous",
        activated_at=NOW - timedelta(days=2),
        previous_verification_until=NOW + timedelta(minutes=1),
    )
    assert_constraint(
        engine,
        insert(credentials).values(
            **credential_values(
                first_identity,
                credential_version=5,
                external_secret_reference="test/previous-5",
                status="Previous",
                activated_at=NOW - timedelta(days=3),
                previous_verification_until=NOW + timedelta(minutes=2),
            )
        ),
        "uq_machine_credential_versions_one_previous",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "Retired"},
        {"status": "Revoked"},
        {"previous_verification_until": NOW + timedelta(minutes=1)},
        {"retired_at": NOW},
        {"revoked_at": NOW},
        {
            "status": "Previous",
            "previous_verification_until": NOW + timedelta(minutes=1),
            "retired_at": NOW,
        },
        {
            "status": "Previous",
            "previous_verification_until": NOW + timedelta(minutes=1),
            "revoked_at": NOW,
        },
        {
            "status": "Previous",
            "activated_at": NOW,
            "previous_verification_until": NOW,
        },
    ],
)
def test_invalid_machine_credential_state_combinations(app_client, overrides) -> None:
    _, _, engine = app_client
    identity_id = insert_identity(engine)
    assert_rejected(
        engine,
        insert(Base.metadata.tables["machine_credential_versions"]).values(
            **credential_values(identity_id, **overrides)
        ),
    )


def test_valid_retired_and_revoked_credentials_insert(app_client) -> None:
    _, _, engine = app_client
    identity_id = insert_identity(engine)
    insert_credential(engine, identity_id, status="Retired", retired_at=NOW)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/revoked",
        status="Revoked",
        revoked_at=NOW,
    )


def test_nonce_constraints_rotation_overlap_and_restrictive_delete(app_client) -> None:
    _, _, engine = app_client
    identity_id = insert_identity(engine)
    current_id = insert_credential(engine, identity_id)
    previous_id = insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW + timedelta(minutes=5),
    )
    nonces = Base.metadata.tables["machine_request_nonces"]
    credentials = Base.metadata.tables["machine_credential_versions"]
    identities = Base.metadata.tables["machine_identities"]
    with engine.begin() as connection:
        connection.execute(insert(nonces).values(**nonce_values(identity_id, current_id)))
    assert_rejected(
        engine,
        insert(nonces).values(
            **nonce_values(
                identity_id,
                previous_id,
                verified_credential_version=2,
            )
        ),
    )
    for overrides in (
        {"nonce_digest": "invalid"},
        {"verified_credential_version": 0, "nonce_digest": "b" * 64},
        {"environment": " ", "nonce_digest": "b" * 64},
        {"expires_at": datetime(2000, 1, 1, tzinfo=UTC), "nonce_digest": "b" * 64},
    ):
        assert_rejected(
            engine,
            insert(nonces).values(**nonce_values(identity_id, current_id, **overrides)),
        )
    assert_rejected(engine, delete(credentials).where(credentials.c.id == current_id))
    assert_rejected(engine, delete(identities).where(identities.c.id == identity_id))
    with engine.connect() as connection:
        received = connection.scalar(select(nonces.c.received_at))
    assert received.tzinfo is not None and received.utcoffset() == timedelta(0)


def test_current_and_previous_credentials_authenticate_and_body_remains_readable(
    app_client,
) -> None:
    _, client, engine = app_client
    identity_id, _ = seed_current(engine)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW + timedelta(seconds=1),
    )
    first = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(),
    )
    second = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(SECRET_PREVIOUS, nonce="nonce-previous-0123456789"),
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == {"service_id": "workflow.test", "body": "payload"}


@pytest.mark.parametrize(
    "case",
    ["wrong-signature", "stale", "future", "tampered-body", "tampered-path", "unknown"],
)
def test_invalid_authentication_is_generic_and_persists_no_nonce(app_client, case) -> None:
    _, client, engine = app_client
    seed_current(engine)
    path = "/__tests__/workflow-auth"
    body = b"payload"
    kwargs = {}
    if case == "stale":
        kwargs["timestamp"] = str(int((NOW - timedelta(seconds=301)).timestamp()))
    elif case == "future":
        kwargs["timestamp"] = str(int((NOW + timedelta(seconds=301)).timestamp()))
    elif case == "unknown":
        kwargs["service_id"] = "workflow.unknown"
    headers = signed_headers(**kwargs)
    if case == "wrong-signature":
        headers["X-Service-Signature"] = "0" * 64
    elif case == "tampered-body":
        body = b"changed"
    elif case == "tampered-path":
        path = "/__tests__/workflow-auth/other"
    response = client.post(path, content=body, headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MACHINE_AUTHENTICATION_FAILED"
    assert "www-authenticate" not in response.headers
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 0
        )


def test_disabled_nonworkflow_and_wrong_environment_fail_generically(app_client) -> None:
    _, client, engine = app_client
    cases = [
        {"status": "Disabled", "disabled_at": NOW, "disable_reason": "test disable"},
        {"service_type": "BackendService"},
        {"environment": "development"},
    ]
    for index, overrides in enumerate(cases):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "TRUNCATE machine_request_nonces, machine_credential_versions, "
                    "machine_identities CASCADE"
                )
            )
        identity_id = insert_identity(engine, **overrides)
        insert_credential(engine, identity_id)
        response = client.post(
            "/__tests__/workflow-auth",
            content=b"payload",
            headers=signed_headers(nonce=f"nonce-case-{index}-0123456789"),
        )
        assert response.status_code == 401


def test_replay_is_rejected_across_credential_versions(app_client) -> None:
    _, client, engine = app_client
    identity_id, _ = seed_current(engine)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW + timedelta(minutes=5),
    )
    nonce = "nonce-replay-0123456789"
    first = client.post(
        "/__tests__/workflow-auth", content=b"payload", headers=signed_headers(nonce=nonce)
    )
    replay = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(SECRET_PREVIOUS, nonce=nonce),
    )
    assert first.status_code == 200
    assert replay.status_code == 401
    with engine.connect() as connection:
        rows = connection.execute(select(Base.metadata.tables["machine_request_nonces"])).all()
    assert len(rows) == 1
    assert nonce not in str(rows)


def test_missing_and_duplicate_headers_fail(app_client) -> None:
    _, client, engine = app_client
    seed_current(engine)
    headers = signed_headers()
    headers.pop("X-Service-Nonce")
    assert (
        client.post("/__tests__/workflow-auth", content=b"payload", headers=headers).status_code
        == 401
    )
    duplicate = list(signed_headers().items()) + [("X-Service-ID", "workflow.test")]
    assert (
        client.post("/__tests__/workflow-auth", content=b"payload", headers=duplicate).status_code
        == 401
    )


def test_previous_after_overlap_fails(app_client) -> None:
    _, client, engine = app_client
    identity_id = insert_identity(engine)
    insert_credential(
        engine,
        identity_id,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW - timedelta(seconds=1),
    )
    response = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(SECRET_PREVIOUS),
    )
    assert response.status_code == 401


@pytest.mark.parametrize("credential_state", ["none", "Previous", "Retired", "Revoked"])
def test_no_effective_current_fails_before_secret_resolution(app_client, credential_state) -> None:
    app, client, engine = app_client
    identity_id = insert_identity(engine)
    if credential_state == "Previous":
        insert_credential(
            engine,
            identity_id,
            external_secret_reference="test/previous",
            status="Previous",
            previous_verification_until=NOW + timedelta(minutes=5),
        )
    elif credential_state == "Retired":
        insert_credential(engine, identity_id, status="Retired", retired_at=NOW)
    elif credential_state == "Revoked":
        insert_credential(engine, identity_id, status="Revoked", revoked_at=NOW)
    resolver = app.state.machine_secret_resolver
    response = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MACHINE_AUTHENTICATION_FAILED"
    assert resolver.calls == []
    assert all(
        detail not in response.text
        for detail in (credential_state, "credential", "activated", "test/current")
    )
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 0
        )


def test_future_activation_fails_closed(app_client) -> None:
    app, client, engine = app_client
    identity_id = insert_identity(engine)
    insert_credential(engine, identity_id, activated_at=NOW + timedelta(microseconds=1))
    response = client.post("/__tests__/workflow-auth", content=b"payload", headers=signed_headers())
    assert response.status_code == 401
    assert app.state.machine_secret_resolver.calls == []


def test_current_with_future_previous_fails_closed(app_client) -> None:
    app, client, engine = app_client
    identity_id, _ = seed_current(engine)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        activated_at=NOW + timedelta(microseconds=1),
        previous_verification_until=NOW + timedelta(minutes=5),
    )
    response = client.post("/__tests__/workflow-auth", content=b"payload", headers=signed_headers())
    assert response.status_code == 401
    assert app.state.machine_secret_resolver.calls == []


def test_activation_and_previous_overlap_boundaries_are_inclusive(app_client) -> None:
    _, client, engine = app_client
    identity_id = insert_identity(engine)
    insert_credential(engine, identity_id, activated_at=NOW)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        activated_at=NOW - timedelta(seconds=1),
        previous_verification_until=NOW,
    )
    current = client.post("/__tests__/workflow-auth", content=b"payload", headers=signed_headers())
    previous = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(SECRET_PREVIOUS, nonce="nonce-boundary-0123456789"),
    )
    assert current.status_code == previous.status_code == 200


def test_previous_expired_one_microsecond_is_ignored(app_client) -> None:
    _, client, engine = app_client
    identity_id, _ = seed_current(engine)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW - timedelta(microseconds=1),
    )
    response = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(SECRET_PREVIOUS),
    )
    assert response.status_code == 401


def test_multiple_matching_candidate_secrets_fail_closed(app_client) -> None:
    app, client, engine = app_client
    identity_id, _ = seed_current(engine)
    insert_credential(
        engine,
        identity_id,
        credential_version=2,
        external_secret_reference="test/previous",
        status="Previous",
        previous_verification_until=NOW + timedelta(minutes=5),
    )
    app.state.machine_secret_resolver = Resolver(
        {"test/current": SECRET_CURRENT, "test/previous": SECRET_CURRENT}
    )
    response = client.post("/__tests__/workflow-auth", content=b"payload", headers=signed_headers())
    assert response.status_code == 401
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 0
        )


def test_query_canonicalization_through_http_boundary(app_client) -> None:
    _, client, engine = app_client
    seed_current(engine)
    equivalent = client.post(
        "/__tests__/workflow-auth?a=1&b=2",
        content=b"payload",
        headers=signed_headers(query=b"b=2&a=1", nonce="nonce-query-one-01234567"),
    )
    repeated = client.post(
        "/__tests__/workflow-auth?z=&a=2&a=1",
        content=b"payload",
        headers=signed_headers(query=b"a=1&z=&a=2", nonce="nonce-query-two-01234567"),
    )
    changed = client.post(
        "/__tests__/workflow-auth?a=changed&b=2",
        content=b"payload",
        headers=signed_headers(query=b"a=1&b=2", nonce="nonce-query-three-012345"),
    )
    added = client.post(
        "/__tests__/workflow-auth?a=1&b=2&c=3",
        content=b"payload",
        headers=signed_headers(query=b"a=1&b=2", nonce="nonce-query-four-0123456"),
    )
    assert equivalent.status_code == repeated.status_code == 200
    assert changed.status_code == added.status_code == 401


@pytest.mark.parametrize("query", ["bad=%GG", "bad=%FF"])
def test_malformed_query_through_http_boundary_is_generic_401(app_client, query) -> None:
    _, client, engine = app_client
    seed_current(engine)
    response = client.post(
        f"/__tests__/workflow-auth?{query}",
        content=b"payload",
        headers=signed_headers(nonce=f"nonce-malformed-{query[-2:]}-012345"),
    )
    assert response.status_code == 401
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 0
        )


def test_secret_and_database_failures_are_safe_503(app_client) -> None:
    app, client, engine = app_client
    seed_current(engine)
    app.state.machine_secret_resolver = Resolver({})
    secret_failure = client.post(
        "/__tests__/workflow-auth", content=b"payload", headers=signed_headers()
    )
    assert secret_failure.status_code == 503
    app.state.machine_secret_resolver = Resolver({"test/current": SECRET_CURRENT})
    original = app.state.session_factory

    def unavailable():
        raise OperationalError("hidden", {}, Exception("hidden"))

    app.state.session_factory = unavailable
    database_failure = client.post(
        "/__tests__/workflow-auth",
        content=b"payload",
        headers=signed_headers(nonce="nonce-database-01234567"),
    )
    app.state.session_factory = original
    assert database_failure.status_code == 503
    combined = secret_failure.text + database_failure.text
    assert all(
        value not in combined for value in ("test/current", "hidden", "machine_", "Traceback")
    )


def test_nonce_remains_consumed_after_downstream_failure(app_client) -> None:
    app, _, engine = app_client
    seed_current(engine)
    client = TestClient(app, raise_server_exceptions=False)
    nonce = "nonce-downstream-01234567"
    response = client.post(
        "/__tests__/workflow-auth-fails",
        content=b"payload",
        headers=signed_headers(path=b"/__tests__/workflow-auth-fails", nonce=nonce),
    )
    assert response.status_code == 500
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 1
        )


def test_nonce_insertion_database_failure_is_safe_503(app_client) -> None:
    app, client, engine = app_client
    seed_current(engine)
    original = app.state.session_factory

    class NonceFailingFactory:
        def __call__(self):
            return original()

        def begin(self):
            raise OperationalError("hidden nonce insert", {}, Exception("hidden detail"))

    app.state.session_factory = NonceFailingFactory()
    nonce = "nonce-insert-failure-012345"
    headers = signed_headers(nonce=nonce)
    response = client.post("/__tests__/workflow-auth", content=b"payload", headers=headers)
    app.state.session_factory = original
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Base.metadata.tables["machine_request_nonces"])
            )
            == 0
        )
    for forbidden in (
        "hidden",
        "workflow.test",
        "test/current",
        nonce,
        headers["X-Service-Signature"],
        "machine_request_nonces",
        "Traceback",
    ):
        assert forbidden not in response.text


def test_human_401_still_advertises_bearer(app_client) -> None:
    _, client, _ = app_client
    response = client.get("/api/v1/service-requests/00000000-0000-0000-0000-000000000001")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
