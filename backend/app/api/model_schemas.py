from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agents.providers.base import MAX_CUSTOM_HEADERS, header_error


# Explicitly selectable profiles; ``auto`` infers one from the endpoint host.
# A Base URL whose path ends in ``/responses`` switches any of these to the
# Responses dialect, so the explicit ``openai-responses`` entry is only needed
# when the URL does not carry that suffix.
ProviderProfileId = Literal[
    "auto", "openai", "deepseek", "openrouter", "opencode", "opencode-go",
    "custom-openai", "openai-responses", "anthropic", "custom-anthropic",
]

# Profiles as materialized in a game snapshot (``auto`` already resolved).
# Every id above except ``auto`` must appear here, or the public snapshot
# validator silently drops that seat's model row.
ResolvedProviderProfileId = Literal[
    "openai", "deepseek", "openrouter", "opencode", "opencode-go",
    "custom-openai", "openai-responses", "anthropic", "custom-anthropic",
]


def validated_headers(value: dict[str, str]) -> dict[str, str]:
    """Reject custom headers the transport would refuse to send anyway."""
    if len(value) > MAX_CUSTOM_HEADERS:
        raise ValueError(
            f"at most {MAX_CUSTOM_HEADERS} custom headers are allowed",
        )
    seen: set[str] = set()
    for name, header_value in value.items():
        reason = header_error(name, header_value)
        if reason is not None:
            raise ValueError(f"header {name!r}: {reason}")
        key = name.lower()
        if key in seen:
            raise ValueError(f"header {name!r}: duplicate header name")
        seen.add(key)
    return value


class ModelConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    base_url: str
    model_id: str = Field(min_length=1)
    api_key: str = ""
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    strict_base_url: Optional[str] = None
    provider_profile: ProviderProfileId = "auto"
    headers: dict[str, str] = Field(default_factory=dict)

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

    @field_validator("headers")
    @classmethod
    def _check_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return validated_headers(value)


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
    headers: dict[str, str] = Field(default_factory=dict)
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
    # ``None`` means "use the stored config's headers" when ``config_id`` is
    # given; the dialog sends the edited rows so a header change can be tested
    # before it is saved.
    headers: Optional[dict[str, str]] = None
    # ``None`` means "use the stored config's strict address" when
    # ``config_id`` is given; an empty string is an edited value that clears
    # it, so the dialog can drop a strict address before saving.
    strict_base_url: Optional[str] = None

    @field_validator("headers")
    @classmethod
    def _check_headers(
        cls, value: Optional[dict[str, str]],
    ) -> Optional[dict[str, str]]:
        return None if value is None else validated_headers(value)


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
