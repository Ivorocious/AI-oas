"""Infrastructure health endpoint."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ai_operations_automation.config import Settings

router = APIRouter()


class HealthResponse(BaseModel):
    """Safe, stable health response without internal configuration details."""

    status: Literal["ok"]
    service: str


def settings_from_request(request: Request) -> Settings:
    """Resolve the explicit settings attached by the application factory."""
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(settings_from_request)]) -> HealthResponse:
    """Report process availability without checking future external dependencies."""
    return HealthResponse(status="ok", service=settings.app_name)
