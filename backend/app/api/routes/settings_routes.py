"""Application settings endpoints.

One read endpoint, one write endpoint, both on the same path and both resolved
by the single implementation in ``app/core/prompt_capture.py`` — the same call
the game service makes when it creates a game, so the interface can never claim
one value while the engine acts on another.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.settings_schemas import (
    PromptCaptureSettingsRequest,
    PromptCaptureSettingsResponse,
)
from app.core.prompt_capture import resolve_capture_setting
from app.stores.app_settings_store import get_app_settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current() -> PromptCaptureSettingsResponse:
    enabled, source = resolve_capture_setting(
        get_app_settings_store().get_prompt_capture_enabled(),
    )
    return PromptCaptureSettingsResponse(enabled=enabled, source=source)


@router.get("/prompt-capture", response_model=PromptCaptureSettingsResponse)
async def get_prompt_capture():
    """The effective training-corpus switch and where it is coming from."""
    return _current()


@router.patch("/prompt-capture", response_model=PromptCaptureSettingsResponse)
async def update_prompt_capture(req: PromptCaptureSettingsRequest):
    """Store, or clear, the interface override.

    The switch takes effect for newly created games only; games already running
    (or interrupted) keep the decision they were created with.
    """
    get_app_settings_store().set_prompt_capture_enabled(req.enabled)
    return _current()
