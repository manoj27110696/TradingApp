from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Options Spread Copilot"
    app_env: str = "local"
    cutemarkets_api_key: str = ""
    cutemarkets_base_url: str = "https://api.cutemarkets.com"
    cutemarkets_chain_strike_window_pct: float = 0.12
    market_chameleon_featured_ideas_url: str = ""
    market_chameleon_session_cookie: str = ""
    default_symbols: str = Field(default="SPY,QQQ,IWM,AAPL,MSFT,NVDA,TSLA")

    @property
    def symbols(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.default_symbols.split(",") if symbol.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
