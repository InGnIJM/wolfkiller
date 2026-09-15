from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


# Explicitly selectable profiles; ``auto`` infers one from the endpoint host.
ProviderProfileId = Literal[
    "auto", "openai", "deepseek", "openrouter", "custom-openai",
    "anthropic", "custom-anthropic",
]

# Profiles as materialized in a game snapshot (``auto`` already resolved).
ResolvedProviderProfileId = Literal[
    "openai", "deepseek", "openrouter", "custom-openai",
    "anthropic", "custom-anthropic",
]


class ModelConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    base_url: str
    model_id: str = Field(min_length=1)
    api_key: str = ""
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    strict_base_url: Optional[str] = None
    provider_profile: ProviderProfileId = "auto"

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("base_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return value


class ModelConfigResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model_id: str
    has_key: bool
    api_key_masked: Optional[str]
    key_invalid: bool
    temperature: Optional[float]
    strict_base_url: Optional[str]
    provider_profile: ProviderProfileId
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    configs: list[ModelConfigResponse]


class ModelTestRequest(BaseModel):
    config_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_id: Optional[str] = None
    # ``None`` means "use the stored config's profile" when ``config_id`` is
    # given, or ``auto`` for an ad-hoc test; an explicit value always wins so
    # the dialog can test a profile change before saving it.
    provider_profile: Optional[ProviderProfileId] = None


class ModelCapabilitiesResponse(BaseModel):
    tools: bool
    strict_tools: bool
    json_output: bool
    reasoning_effort: bool
    temperature: bool


class ModelTestResponse(BaseModel):
    ok: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    capabilities: Optional[ModelCapabilitiesResponse] = None


class ModelAssignment(BaseModel):
    config_id: Optional[str] = None
    count: Annotated[int, Field(strict=True, ge=1)]


class ModelSnapshotEntry(BaseModel):
    """Public, credential-free snapshot of one model assignment group."""

    config_id: Optional[str] = None
    name: str
    model_id: str
    base_url: str
    provider_profile: ResolvedProviderProfileId
    count: Annotated[int, Field(strict=True, ge=1)]
    seats: list[Annotated[int, Field(strict=True, ge=1)]]


def public_model_snapshot(raw: object) -> list[dict]:
    """Return credential-free v2 snapshot rows; skip unknown/legacy shapes."""
    if not isinstance(raw, list):
        return []
    published: list[dict] = []
    for item in raw:
        try:
            published.append(ModelSnapshotEntry.model_validate(item).model_dump())
        except ValidationError:
            continue
    return published
