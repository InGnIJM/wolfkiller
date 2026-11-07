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
    "jev", "custom-system-one",
]

# Profiles as materialized in a game snapshot (``auto`` already resolved).
# Every id above except ``auto`` must appear here, or the public snapshot
# validator silently drops that seat's model row.
ResolvedProviderProfileId = Literal[
    "openai", "deepseek", "openrouter", "opencode", "opencode-go",
    "custom-openai", "openai-responses", "anthropic", "custom-anthropic",
    "jev", "custom-system-one",
]

# Values sent verbatim as ``reasoning_effort`` on chat-completions calls;
# ``none`` is the DeepSeek spelling for "thinking off". An unset field sends
# nothing at all, leaving the provider's own default in place.
ReasoningEffortId = Literal["none", "low", "medium", "high", "max"]


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
    reasoning_effort: Optional[ReasoningEffortId] = None
    headers: dict[str, str] = Field(default_factory=dict)
    #: Full model list this provider serves; ``model_ids[0]`` is the stored
    #: default. ``None`` keeps the single-model shape (``[model_id]``).
    model_ids: Optional[list[str]] = None

    @field_validator("model_ids")
    @classmethod
    def _check_model_ids(cls, value: Optional[list[str]]) -> Optional[list[str]]:
        if value is None:
            return None
        ids = [item.strip() for item in value if item.strip()]
        if not ids:
            raise ValueError("model_ids must contain at least one model")
        return list(dict.fromkeys(ids))

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


class ModelLatencyStats(BaseModel):
    """Observed call latency for one model, used to budget a benchmark game.

    ``avg_ms`` drives the wall-clock estimate; the percentiles are published
    so the wizard can show how heavy the tail is without recomputing it.
    """

    samples: int
    avg_ms: int
    p50_ms: int
    p90_ms: int


class ModelConfigResponse(BaseModel):
    id: str
    name: str
    base_url: str
    model_id: str
    model_ids: list[str] = Field(default_factory=list)
    has_key: bool
    api_key_masked: Optional[str]
    key_invalid: bool
    temperature: Optional[float]
    strict_base_url: Optional[str]
    provider_profile: ProviderProfileId
    reasoning_effort: Optional[str]
    headers: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    # ``None`` when this model has too little recorded history to estimate.
    latency: Optional[ModelLatencyStats] = None


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
    # Same omission rule as ``strict_base_url``: ``None`` falls back to the
    # stored config, ``""`` clears it, anything else must be a valid level.
    reasoning_effort: Optional[str] = None

    @field_validator("reasoning_effort")
    @classmethod
    def _check_reasoning_effort(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in ("", *ReasoningEffortId.__args__):
            raise ValueError(
                "reasoning_effort must be one of "
                + ", ".join(ReasoningEffortId.__args__),
            )
        return value

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
    """One seat group: provider config plus an optional per-selection model.

    ``model_id`` / ``reasoning_effort`` override the values stored on the
    referenced config so one provider entry can serve several models (and
    thinking levels) inside the same game.
    """

    config_id: Optional[str] = None
    count: Annotated[int, Field(strict=True, ge=1)]
    model_id: Optional[str] = None
    reasoning_effort: Optional[ReasoningEffortId] = None

    @field_validator("model_id")
    @classmethod
    def _check_model_id(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("model_id must be a non-empty string")
        return value.strip() if value is not None else None


class ModelCatalogRequest(BaseModel):
    """Pull a provider's ``/models`` listing for a stored config (``config_id``)
    or an unsaved dialog (inline ``base_url``/``api_key``), mirroring the
    ``/test`` override rules so a stored key never reaches a changed endpoint."""

    config_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    provider_profile: Optional[ProviderProfileId] = None
    strict_base_url: Optional[str] = None
    headers: Optional[dict[str, str]] = None

    @field_validator("headers")
    @classmethod
    def _check_headers(
        cls, value: Optional[dict[str, str]],
    ) -> Optional[dict[str, str]]:
        return None if value is None else validated_headers(value)


class ModelCatalogResponse(BaseModel):
    """Model ids exposed by a provider's ``GET /models`` listing."""

    model_ids: list[str] = Field(default_factory=list)
    error: Optional[str] = None


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
