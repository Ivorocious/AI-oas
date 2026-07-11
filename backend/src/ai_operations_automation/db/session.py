"""Replaceable synchronous session-factory construction."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Bind a synchronous session factory to an explicitly provided engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
