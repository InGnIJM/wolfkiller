import os
from enum import StrEnum
from dotenv import load_dotenv

load_dotenv()


class PipelineMode(StrEnum):
    V1 = "v1"
    SHADOW = "shadow"
    V2 = "v2"


def pipeline_mode_from_env(value: str | None = None) -> PipelineMode:
    raw = os.getenv("ROLE_PIPELINE_V2", "v1") if value is None else value
    if type(raw) is not str:
        raise TypeError("pipeline mode must be a string")
    try:
        return PipelineMode(raw)
    except ValueError:
        raise ValueError("pipeline mode must be v1, shadow, or v2") from None


class LLMConfig:
    provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    strict_base_url: str = os.getenv(
        "DEEPSEEK_STRICT_BASE_URL", "https://api.deepseek.com/beta"
    )

    @property
    def models(self) -> list[str]:
        """Available models for random assignment across players."""
        models_env = os.getenv("LLM_MODELS", "")
        if models_env:
            return [m.strip() for m in models_env.split(",") if m.strip()]
        # Fall back to single model
        single = os.getenv("LLM_MODEL", "")
        if single:
            return [single]
        return ["deepseek-v4-pro"]

    temperature: float = float(os.getenv("LLM_TEMPERATURE", "1.2"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "768"))
    action_timeout_seconds: float = float(os.getenv("LLM_ACTION_TIMEOUT_SECONDS", "90"))
    action_retry_timeout_seconds: float = float(
        os.getenv("LLM_ACTION_RETRY_TIMEOUT_SECONDS", "60")
    )


class GameConfig:
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1
    speech_timeout_seconds: int = 60
    phase_delay_seconds: float = 0.3
    vote_concurrency: int = int(os.getenv("VOTE_CONCURRENCY", "5"))
    vote_phase_timeout_seconds: float = float(
        os.getenv("VOTE_PHASE_TIMEOUT_SECONDS", "180")
    )


class AppConfig:
    llm: LLMConfig = LLMConfig()
    game: GameConfig = GameConfig()
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = AppConfig()
