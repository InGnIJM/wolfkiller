from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ModelConfigRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    base_url: str
    model_id: str = Field(min_length=1)
    api_key: str = ""
    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    strict_base_url: Optional[str] = None
    provider_profile: Literal[
        "auto", "openai", "deepseek", "openrouter", "custom-openai",
    ] = "auto"

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
    provider_profile: Literal[
        "auto", "openai", "deepseek", "openrouter", "custom-openai",
    ]
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    configs: list[ModelConfigResponse]


class ModelTestRequest(BaseModel):
    config_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_id: Optional[str] = None
    provider_profile: Literal[
        "auto", "openai", "deepseek", "openrouter", "custom-openai",
    ] = "auto"


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
    count: int = Field(ge=1)
