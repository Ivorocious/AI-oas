"""Alembic environment for the explicit PostgreSQL persistence boundary."""

from sqlalchemy import engine_from_config, pool

from ai_operations_automation.config import Settings
from ai_operations_automation.db import models  # noqa: F401
from ai_operations_automation.db.base import Base
from alembic import context

config = context.config
target_metadata = Base.metadata


def configured_database_url() -> str:
    """Resolve the migration URL through the same typed, project-prefixed settings."""
    return str(Settings().database_url)


def run_migrations_offline() -> None:
    """Run migrations without creating an Engine."""
    context.configure(
        url=configured_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a short-lived Alembic-managed connection."""
    config.set_main_option("sqlalchemy.url", configured_database_url().replace("%", "%%"))
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
