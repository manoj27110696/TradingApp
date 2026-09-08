from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Options Spread Copilot"
    app_env: str = "local"
    scan_timeout_seconds: int = 45
    max_scan_symbols: int = 5
    scan_concurrency: int = 3
    market_chameleon_featured_ideas_url: str = ""
    market_chameleon_session_cookie: str = ""
    market_chameleon_max_age_days: int = 7
    public_rate_limit_requests: int = 60
    public_rate_limit_window_seconds: int = 60
    default_symbols: str = Field(default="SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA")

    @property
    def symbols(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.default_symbols.split(",") if symbol.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
