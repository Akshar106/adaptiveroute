import pytest
from pydantic import ValidationError

from adaptiveroute.config import Settings


def make(**env: str) -> Settings:
    # _env_file=None so a developer's local .env never leaks into tests
    return Settings(_env_file=None, **env)  # type: ignore[call-arg, arg-type]


def test_celery_urls_derived_from_redis_url() -> None:
    s = make(redis_url="redis://cache:6379/0")
    assert s.celery_broker_url == "redis://cache:6379/1"
    assert s.celery_result_backend == "redis://cache:6379/2"


def test_celery_urls_without_db_suffix() -> None:
    s = make(redis_url="redis://cache:6379")
    assert s.celery_broker_url == "redis://cache:6379/1"


def test_prod_rejects_default_pepper() -> None:
    with pytest.raises(ValidationError, match="AR_API_KEY_PEPPER"):
        make(env="prod")


def test_prod_accepts_real_pepper() -> None:
    s = make(env="prod", api_key_pepper="s3cr3t-from-secrets-manager")
    assert s.env == "prod"


def test_groq_api_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AR_LLM_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_configured
    assert s.llm_api_key is not None
    assert s.llm_api_key.get_secret_value() == "gsk_test"
    # never rendered in reprs/logs
    assert "gsk_test" not in repr(s)


def test_llm_not_configured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("AR_LLM_API_KEY", raising=False)
    assert not make().llm_configured


def test_cors_origins_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AR_CORS_ORIGINS", "https://a.example, https://b.example")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.cors_origins == ["https://a.example", "https://b.example"]
