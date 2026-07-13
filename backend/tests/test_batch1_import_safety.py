import importlib

import psycopg


def test_callback_and_replacement_module_imports_do_not_connect_to_postgres(monkeypatch) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("callback boundary imports must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    modules = (
        "ai_operations_automation.attempt_callbacks.authorization",
        "ai_operations_automation.attempt_callbacks.models",
        "ai_operations_automation.attempt_callbacks.parsing",
        "ai_operations_automation.callback_credentials.models",
        "ai_operations_automation.callback_credentials.parsing",
        "ai_operations_automation.callback_credentials.service",
        "ai_operations_automation.api.callback_credentials",
    )
    for module_name in modules:
        assert importlib.import_module(module_name).__name__ == module_name


def test_main_import_remains_database_connection_free_with_batch1_modules(monkeypatch) -> None:
    def unexpected_connect(*args, **kwargs):
        raise AssertionError("main import must not connect")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    import ai_operations_automation.main as main

    assert importlib.reload(main).app.title == "AI Operations Automation API"
