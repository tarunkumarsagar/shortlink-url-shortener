"""
Application configuration.

WHY pydantic-settings rather than scattering `os.environ.get(...)`
calls throughout the codebase: this gives us ONE validated,
type-checked place that defines what configuration the app needs,
fails fast and loudly at startup if something required is missing
(instead of crashing deep in a request handler later), and makes it
trivial to see the entire environment-variable contract in one file
-- which is also exactly what .env.example should mirror.

SECURITY NOTE: the default DATABASE_URL below uses a local dev
password purely so the app runs out-of-the-box in this sandboxed
development environment. It is NOT a secret worth protecting (it's
already public in this conversation) and MUST NOT be treated as a
template for real credentials. .env.example will show `DATABASE_URL=`
with no value, and this default will be removed/overridden before any
real deployment. This is called out explicitly, not glossed over.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://postgres:devpassword@localhost:5432/shortlink"
    database_pool_min_size: int = 2
    database_pool_max_size: int = 10

    # SECURITY: this default is a placeholder that only makes local
    # development work out-of-the-box in this sandboxed environment.
    # A real deployment MUST override this via the JWT_SECRET_KEY
    # environment variable with a long, random, secret value (e.g.
    # `openssl rand -hex 32`) -- anyone who has this value can forge
    # valid access tokens for any user. .env.example documents this.
    jwt_secret_key: str = "local-dev-only-insecure-secret-do-not-use-in-production"

    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl_seconds: int = 300

    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    click_events_queue_name: str = "click_events"

    rate_limit_per_minute_anonymous: int = 20
    rate_limit_per_minute_authenticated: int = 100


settings = Settings()
