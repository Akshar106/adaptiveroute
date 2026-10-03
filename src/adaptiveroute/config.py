"""Environment-based configuration.

All runtime configuration comes from environment variables (prefixed ``AR_``) or a
local ``.env`` file. Agent definitions and router weights live in YAML files under
``config/`` because they are structured data that should be versioned with the code
and referenced by benchmark reports.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "test", "prod"]
StrategyName = Literal["round_robin", "embedding", "llm", "adaptive"]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = "dev"
    service_name: str = "adaptiveroute"
    log_level: str = "INFO"
    log_json: bool = True

    # --- storage -------------------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://adaptiveroute:adaptiveroute@localhost:5432/adaptiveroute"
    )
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- LLM provider (any OpenAI-compatible endpoint; Groq by default) ------
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AR_LLM_API_KEY", "GROQ_API_KEY")
    )
    llm_connect_timeout_s: float = Field(default=5.0, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1, le=10)
    llm_backoff_base_s: float = Field(default=0.5, gt=0)
    llm_backoff_max_s: float = Field(default=20.0, gt=0)

    # --- embeddings ------------------------------------------------------------
    embedding_backend: Literal["fastembed", "hashing"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_cache_dir: Path = PROJECT_ROOT / ".cache" / "fastembed"
    embedding_cache_ttl_s: int = 7 * 24 * 3600

    # --- routing -----------------------------------------------------------------
    agents_config_path: Path = PROJECT_ROOT / "config" / "agents.yaml"
    routing_config_path: Path = PROJECT_ROOT / "config" / "routing.yaml"
    default_strategy: StrategyName = "adaptive"
    sync_request_timeout_s: float = Field(default=90.0, gt=0)
    response_cache_ttl_s: int = 3600

    # --- API security / limits -----------------------------------------------
    api_key_pepper: SecretStr = SecretStr("dev-only-pepper-change-me")
    rate_limit_per_minute: int = Field(default=60, ge=1)
    rate_limit_burst: int = Field(default=20, ge=1)
    idempotency_ttl_hours: int = Field(default=24, ge=1)
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://localhost:8000",
    ]
    max_query_chars: int = Field(default=4000, ge=1)

    # --- observability -------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_endpoint: str = "http://localhost:4317"
    trace_query_url: str | None = "http://localhost:16686"  # Jaeger query API
    metrics_enabled: bool = True

    # --- benchmarks ----------------------------------------------------------
    benchmark_dir: Path = PROJECT_ROOT / "benchmarks"
    dataset_path: Path = PROJECT_ROOT / "data" / "eval" / "dataset.jsonl"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str) and not v.startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _derive_and_check(self) -> Settings:
        if self.celery_broker_url is None:
            self.celery_broker_url = _with_redis_db(self.redis_url, 1)
        if self.celery_result_backend is None:
            self.celery_result_backend = _with_redis_db(self.redis_url, 2)
        if self.env == "prod" and self.api_key_pepper.get_secret_value().startswith("dev-only"):
            raise ValueError("AR_API_KEY_PEPPER must be set to a real secret in prod")
        return self

    @property
    def llm_configured(self) -> bool:
        return self.llm_api_key is not None and bool(self.llm_api_key.get_secret_value())


def _with_redis_db(url: str, db: int) -> str:
    """Return ``url`` pointed at a different logical Redis database."""
    base, _, last = url.rpartition("/")
    if base.startswith("redis") and last.isdigit():
        return f"{base}/{db}"
    return f"{url.rstrip('/')}/{db}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
