from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="STUDYHUB_")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    migration_database_url: SecretStr | None = None
    cache_url: SecretStr
    control_url: SecretStr
    broker_url: SecretStr
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_issuer: str = "smart-study-hub"
    jwt_audience: str = "smart-study-api"
    access_token_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_days: int = Field(default=7, ge=1, le=30)
    # Conservative development budget; multiply by process count before deployment.
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    namespace: str = Field(default="studyhub:dev", pattern=r"^[a-zA-Z0-9:_-]+$")
    allowed_origins: list[str] = ["http://localhost:3000"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    integration_base_url: str | None = None
    integration_token: SecretStr | None = None
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str | None = None
    oidc_endpoint_origins: list[str] = []

    @model_validator(mode="after")
    def validate_connections(self):
        if make_url(self.database_url.get_secret_value()).drivername != "postgresql+asyncpg":
            raise ValueError("database_url must use postgresql+asyncpg")
        for url in (self.cache_url, self.control_url, self.broker_url):
            if not url.get_secret_value().startswith(("redis://", "rediss://")):
                raise ValueError("Redis URLs must use redis:// or rediss://")
        if self.environment == "production":
            if "*" in self.allowed_origins or "*" in self.allowed_hosts:
                raise ValueError("Wildcard origins/hosts are forbidden in production")
            if (
                len(
                    set(
                        u.get_secret_value()
                        for u in (self.cache_url, self.control_url, self.broker_url)
                    )
                )
                != 3
            ):
                raise ValueError("Cache, control and broker must have distinct URLs")
            if any(not origin.startswith("https://") for origin in self.allowed_origins):
                raise ValueError("Production browser origins must use HTTPS")
        if self.integration_base_url and not self.integration_base_url.startswith("https://"):
            raise ValueError("Third-party integrations must use a fixed HTTPS base URL")
        oidc = (self.oidc_issuer, self.oidc_client_id, self.oidc_redirect_uri)
        if any(oidc) and not all(oidc):
            raise ValueError("OIDC issuer, client ID and redirect URI must be configured together")
        if self.oidc_issuer:
            for url in [self.oidc_issuer, self.oidc_redirect_uri, *self.oidc_endpoint_origins]:
                parsed = urlsplit(url)
                if parsed.username or parsed.password or parsed.fragment or parsed.query:
                    raise ValueError("OIDC URLs cannot contain credentials, queries or fragments")
                if parsed.scheme != "https" and not (
                    self.environment == "test"
                    and parsed.scheme == "http"
                    and parsed.hostname in {"127.0.0.1", "localhost"}
                ):
                    raise ValueError(
                        "OIDC URLs must use HTTPS (loopback HTTP allowed only in tests)"
                    )
        return self

    @property
    def sync_database_url(self) -> str:
        return (
            make_url(self.database_url.get_secret_value())
            .set(drivername="postgresql+psycopg")
            .render_as_string(hide_password=False)
        )

    @property
    def sync_migration_url(self) -> str:
        return (
            make_url((self.migration_database_url or self.database_url).get_secret_value())
            .set(drivername="postgresql+psycopg")
            .render_as_string(hide_password=False)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
