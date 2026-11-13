from __future__ import annotations

from functools import lru_cache
from typing import Dict, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LINK_SECRET_MIN_LEN = 32
INTAKE_KEY_MIN_LEN = 24
NON_BLANK_SECRETS = (
    "tool_webhook_secret", "cal_webhook_secret", "slack_signing_secret", "wa_app_secret",
    "wa_verify_token", "tool_api_key", "anthropic_api_key", "zerobounce_api_key",
    "wa_access_token",
)


class Settings(BaseSettings):
    """Environment-driven settings. Secrets have no defaults and are required.

    Dict fields (link_targets, campaign_ids) are JSON-encoded env vars.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # Core
    database_url: str
    link_secret: str
    link_base_url: str
    link_targets: Dict[str, str]

    # Email tool
    tool_name: Literal["instantly", "smartlead"]
    tool_api_key: str
    tool_webhook_secret: str
    campaign_ids: Dict[str, str]
    default_tz_group: str

    # Claude
    anthropic_api_key: str
    classifier_model: str = "claude-sonnet-5-5"
    chatbot_model: str = "claude-sonnet-5-5"

    # Intake
    zerobounce_api_key: str
    intake_api_key: str

    # Cal.com
    cal_webhook_secret: str
    cal_booking_url: str

    # Slack
    slack_webhook_url: str
    slack_signing_secret: str
    # comma-separated Slack user ids allowed to use /wa and /wa-done ("" = anyone in the workspace)
    slack_allowed_user_ids: str = ""

    # WhatsApp
    wa_phone_number_id: str
    wa_access_token: str
    wa_app_secret: str
    wa_verify_token: str
    wa_graph_version: str = "v21.0"

    # Channels and hours
    primary_when_both: Literal["whatsapp", "email"] = "whatsapp"
    business_tz: str = "Asia/Kolkata"
    business_hours: str = "Mon-Fri 09:00-18:00"

    # Approvals: EU cold email needs legal sign-off; until then EU leads get no email
    eu_email_approved: bool = False

    @field_validator("link_secret")
    @classmethod
    def _link_secret_strong(cls, v: str) -> str:
        if len(v) < LINK_SECRET_MIN_LEN:
            raise ValueError(f"link_secret must be at least {LINK_SECRET_MIN_LEN} characters")
        return v

    @field_validator("intake_api_key")
    @classmethod
    def _intake_key_strong(cls, v: str) -> str:
        if len(v.strip()) < INTAKE_KEY_MIN_LEN:
            raise ValueError(f"intake_api_key must be at least {INTAKE_KEY_MIN_LEN} characters")
        return v

    @field_validator(*NON_BLANK_SECRETS, "intake_api_key")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        if not v.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
