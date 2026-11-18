from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    lease_seconds: float = Field(default=15, ge=1, le=300)
    recovery_poll_seconds: float = Field(default=2, gt=0, le=30)
    model_concurrency: int = Field(default=4, ge=1, le=64)
    max_active_campaigns: int = Field(default=16, ge=1, le=128)
    candidate_timeout_seconds: float = Field(default=45, gt=0, le=300)
    shutdown_grace_seconds: float = Field(default=5, ge=0, le=60)
    access_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./agent_platform.db"
    ai_provider: str = "mock"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_max_tokens: int = 600
    llm_timeout_seconds: float = 30
    support_timeout_seconds: float = Field(default=90,gt=0,le=180)
    llm_max_calls: int = 18
    llm_budget_cny: float = 10
    llm_audit_path: str = "./artifacts/llm-usage.jsonl"
    openai_api_key: str = ""
    local_model_base_url: str = "http://127.0.0.1:11434/v1"
    local_model_name: str = "local-model"
    local_model_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    clickhouse_url: str = ""
    clickhouse_database: str = "flowpilot"
    clickhouse_user: str = "flowpilot"
    clickhouse_password: str = ""
    redis_url: str = ""
    s3_endpoint_url: str = ""
    s3_access_key: str = "demoaccess"
    s3_secret_key: str = "demosecret"
    s3_bucket: str = "agent-artifacts"
    artifact_dir: str = "./artifacts"
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
