import importlib

import psycopg
from sqlalchemy.orm import Session


def test_retry_terminal_and_stale_service_imports_create_no_session_or_connection(
    monkeypatch,
) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("service imports must not connect to PostgreSQL")

    def unexpected_session(*args, **kwargs):
        raise AssertionError("service imports must not construct database sessions")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    monkeypatch.setattr(Session, "__init__", unexpected_session)
    modules = (
        "ai_operations_automation.retry_ai.service",
        "ai_operations_automation.terminal_failure.service",
        "ai_operations_automation.stale_attempts.service",
    )
    for module_name in modules:
        module = importlib.import_module(module_name)
        assert importlib.reload(module) is module
