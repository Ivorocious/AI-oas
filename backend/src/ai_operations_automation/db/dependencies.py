"""FastAPI access to the replaceable synchronous session factory."""

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory
