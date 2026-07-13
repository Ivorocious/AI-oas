import importlib

import psycopg
from sqlalchemy import Engine

from ai_operations_automation.config import Settings
from ai_operations_automation.db import (
    Base,
    create_database_engine,
    create_session_factory,
    models,  # noqa: F401
)

EXPECTED_TABLES = {
    "accepted_intake_keys",
    "audit_events",
    "contacts",
    "inbound_deliveries",
    "outbox_messages",
    "service_requests",
    "application_actors",
    "application_actor_role_assignments",
    "logical_operations",
    "integration_attempts",
    "attempt_callback_credentials",
    "ai_interpretations",
    "machine_identities",
    "machine_credential_versions",
    "machine_request_nonces",
    "command_idempotency_records",
    "failure_recovery_policy_versions",
    "decision_policy_versions",
    "duplicate_candidates",
    "reviewed_fact_sets",
    "routing_decisions",
    "routing_decision_duplicate_candidates",
}

NEW_AI_TABLES = {
    "logical_operations",
    "integration_attempts",
    "attempt_callback_credentials",
    "ai_interpretations",
}


def test_engine_and_session_construction_do_not_connect(
    monkeypatch,
) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("engine construction must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    engine = create_database_engine(Settings(_env_file=None).database_url)
    session_factory = create_session_factory(engine)

    assert isinstance(engine, Engine)
    assert session_factory.kw["bind"] is engine
    assert session_factory.kw["autoflush"] is False
    assert session_factory.kw["expire_on_commit"] is False
    engine.dispose()


def test_model_metadata_contains_exactly_twenty_two_application_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert NEW_AI_TABLES <= set(Base.metadata.tables)


def test_constraint_and_index_names_are_deterministic() -> None:
    for table in Base.metadata.tables.values():
        assert all(constraint.name for constraint in table.constraints)
        assert all(index.name for index in table.indexes)

    accepted = Base.metadata.tables["accepted_intake_keys"]
    assert "uq_accepted_intake_scope_key_digest" in {
        constraint.name for constraint in accepted.constraints
    }
    service_requests = Base.metadata.tables["service_requests"]
    assert "uq_service_request_origin_delivery" in {
        constraint.name for constraint in service_requests.constraints
    }
    routing_decisions = Base.metadata.tables["routing_decisions"]
    assert "fk_routing_decision_policy_identity" in {
        constraint.name for constraint in routing_decisions.constraints
    }
    assert "uq_routing_decisions_request_number" in {
        constraint.name for constraint in routing_decisions.constraints
    }


def test_importing_main_does_not_connect_to_postgres(monkeypatch) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("importing main must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    import ai_operations_automation.main as main

    reloaded = importlib.reload(main)

    assert reloaded.app.title == "AI Operations Automation API"


def test_persistence_metadata_has_no_plaintext_or_secret_column_names() -> None:
    forbidden = {
        "plaintext",
        "raw_token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret_value",
        "private_key",
        "hmac_secret",
        "raw_signature",
        "raw_nonce",
        "body_digest",
        "request_body",
    }
    assert not {
        column.name
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name in forbidden
    }
