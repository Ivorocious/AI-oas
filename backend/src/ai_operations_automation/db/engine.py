"""Explicit SQLAlchemy engine construction."""

from pydantic import PostgresDsn
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL


def create_database_engine(database_url: PostgresDsn | URL | str) -> Engine:
    """Create an engine without opening a database connection."""
    return create_engine(str(database_url), pool_pre_ping=True)
