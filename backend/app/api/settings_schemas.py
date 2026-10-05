"""Schemas for the application settings endpoints.

Both models are closed sets. The response in particular must stay at
``enabled`` + ``source``: a settings endpoint has no business carrying prompt
text, file paths, or game ids, and ``extra="forbid"`` turns that intent into a
validation error instead of a review note.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PromptCaptureSource = Literal["default", "env", "ui"]


class _SettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PromptCaptureSettingsResponse(_SettingsResponse):
    """The effective switch plus where it came from."""

    enabled: bool
    source: PromptCaptureSource


class PromptCaptureSettingsRequest(BaseModel):
    #: ``None`` (or an absent field) clears the interface override, which hands
    #: control back to the environment variable and then the built-in default.
    enabled: Optional[Annotated[bool, Field(strict=True)]] = None
