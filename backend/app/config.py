import os
from dotenv import load_dotenv

load_dotenv()


class LLMConfig:
    provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "1.2"))
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "1024"))


class GameConfig:
    num_werewolves: int = 3
    num_villagers: int = 3
    num_seers: int = 1
    num_witches: int = 1
    num_hunters: int = 1
    speech_timeout_seconds: int = 60
    phase_delay_seconds: float = 2.0


class AppConfig:
    llm: LLMConfig = LLMConfig()
    game: GameConfig = GameConfig()
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = AppConfig()
