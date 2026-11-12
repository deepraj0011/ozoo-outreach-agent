from __future__ import annotations

from functools import lru_cache
from typing import Dict, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Secrets have no defaults and are required.

    Dict fields (link_targets, campaign_ids) are JSON-encoded env vars.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
