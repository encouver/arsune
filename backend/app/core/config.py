import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The tests set ARSUNE_ENV_FILE to an empty value, so a developer's .env cannot change them.
ENV_FILE = os.environ.get("ARSUNE_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARSUNE_", env_file=ENV_FILE, extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+psycopg://arsune:arsune@127.0.0.1:55432/arsune"
    redis_url: str = "redis://127.0.0.1:56379/0"
    evidence_dir: Path = Path(".data/evidence")
    demo_url: str = "http://127.0.0.1:8541"
    ui_origin: str = "http://127.0.0.1:3540"
    lease_seconds: int = Field(default=30, ge=3, le=300)
    job_deadline_seconds: int = Field(default=300, ge=10, le=1800)
    max_attempts: int = Field(default=3, ge=1, le=5)
    coordinator_interval: float = 2
    model_base_url: str = ""
    model_api_key: str = ""
    model_name: str = ""
    model_max_output_tokens: int = Field(default=1800, ge=1, le=8000)
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    model_input_usd_per_million: float | None = None
    model_output_usd_per_million: float | None = None
    model_cost_ceiling_usd: float = Field(default=0.25, gt=0, le=10)
    settlement_mode: Literal["dry", "near_intents", "escrow"] = "dry"
    near_account_id: str = ""
    near_private_key: SecretStr = SecretStr("")
    near_rpc_url: str = "https://free.rpc.fastnear.com"
    intents_contract: str = "intents.near"
    bounty_token: str = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
    max_payment_amount: int = Field(default=1_000_000, gt=0)
    # Escrow mode (docs/ESCROW.md): the contract account, and the terms Arsune gives each bounty.
    escrow_contract: str = ""
    escrow_claims: int = Field(default=50, ge=1, le=1000)  # storage prepaid by Arsune
    escrow_bounty_days: int = Field(default=30, ge=1, le=365)
    swarm_accounts_file: Path = Path(".data/swarm-accounts.json")

    @property
    def model_enabled(self) -> bool:
        from app.agents.model import model_configuration_error

        return model_configuration_error() is None


settings = Settings()
