from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Crypto Alert System"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"

    # Session
    SESSION_MAX_AGE: int = 86400 * 7  # 7 days

    # Registration
    REGISTRATION_ENABLED: bool = True

    # First admin seed (used once on first startup if no users exist)
    FIRST_ADMIN_EMAIL: str = ""
    FIRST_ADMIN_PASSWORD: str = ""
    FIRST_ADMIN_USERNAME: str = "admin"

    # Database
    POSTGRES_HOST: str = "db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "crypto_alerts"
    POSTGRES_USER: str = "crypto_user"
    POSTGRES_PASSWORD: str = "change-me-in-production"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Kraken
    KRAKEN_API_BASE_URL: str = "https://api.kraken.com/0/public"
    KRAKEN_POLL_INTERVAL_SECONDS: int = 60
    # Human-readable pair → Kraken query name
    # Override in .env as a comma-separated list of "HUMAN:KRAKEN" pairs
    KRAKEN_PAIRS: str = "BTC/USD:XBTUSD,ETH/USD:ETHUSD,TAO/USD:TAOUSD,FET/USD:FETUSD"

    # Dynamic asset discovery
    KRAKEN_ASSET_CACHE_HOURS: int = 6
    ALLOW_CUSTOM_PAIRS: bool = False
    FEATURED_PAIRS: str = "BTC/USD,ETH/USD,SOL/USD,ADA/USD,XRP/USD,DOGE/USD"

    @property
    def featured_pairs(self) -> list[str]:
        return [p.strip() for p in self.FEATURED_PAIRS.split(",") if p.strip()]

    @property
    def kraken_pair_map(self) -> dict[str, str]:
        """Returns {human_pair: kraken_name}, e.g. {"BTC/USD": "XBTUSD"}"""
        result = {}
        for entry in self.KRAKEN_PAIRS.split(","):
            entry = entry.strip()
            if ":" in entry:
                human, kraken = entry.split(":", 1)
                result[human.strip()] = kraken.strip()
        return result

    # SMTP
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "alerts@example.com"
    SMTP_TLS: bool = True

    # Security / rate limiting
    LOGIN_MAX_ATTEMPTS: int = 5        # failures before lockout
    LOGIN_WINDOW_MINUTES: int = 15     # sliding window for counting failures
    LOGIN_LOCKOUT_MINUTES: int = 30    # how long the lockout lasts
    HTTPS_ONLY: bool = False           # set True behind HTTPS in production
    SESSION_IDLE_TIMEOUT_HOURS: int = 8        # re-auth after inactivity
    SESSION_ABSOLUTE_TIMEOUT_HOURS: int = 168  # 7 days hard limit

    # Alerts
    ALERT_COOLDOWN_MINUTES: int = 60

    # Price history
    PRICE_SNAPSHOT_INTERVAL_MINUTES: int = 5
    PRICE_HISTORY_RETENTION_DAYS: int = 30
    CHART_DEFAULT_RANGE: str = "24h"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
