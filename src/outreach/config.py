from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from typing import Dict, FrozenSet, Literal
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LINK_SECRET_MIN_LEN = 32
INTAKE_KEY_MIN_LEN = 24
TEAMS_SECRET_MIN_BYTES = 16
NON_BLANK_SECRETS = (
    "tool_webhook_secret", "cal_webhook_secret", "teams_outgoing_secret", "wa_app_secret",
    "wa_verify_token", "tool_api_key", "anthropic_api_key", "zerobounce_api_key",
    "wa_access_token",
)
NON_BLANK_IDS = ("teams_tenant_id", "teams_sales_channel_id")


def _split_ids(raw: str) -> FrozenSet[str]:
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


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

    # Microsoft Teams
    # Workflows "When a Teams webhook request is received" URL; its sig= query part is a secret
    teams_webhook_url: str
    # outgoing-webhook security token (base64), HMAC key for POST /teams/commands
    teams_outgoing_secret: str
    teams_tenant_id: str
    teams_sales_channel_id: str
    # comma-separated Entra (AAD) object ids allowed to use the wa / done commands
    teams_allowed_aad_ids: str

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

    @field_validator(*NON_BLANK_SECRETS, *NON_BLANK_IDS, "intake_api_key")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        if not v.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return v

    @field_validator("teams_webhook_url")
    @classmethod
    def _teams_url_https(cls, v: str) -> str:
        # never echo the value: the sig= query parameter is a secret
        try:
            parts = urlsplit(v.strip())
        except ValueError:
            parts = None
        if parts is None or parts.scheme != "https" or not parts.hostname or " " in v.strip():
            raise ValueError("teams_webhook_url must be an https URL")
        return v.strip()

    @field_validator("teams_outgoing_secret")
    @classmethod
    def _teams_secret_base64(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("teams_outgoing_secret must not be blank")
        try:
            key = base64.b64decode(v.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("teams_outgoing_secret must be base64") from None
        if len(key) < TEAMS_SECRET_MIN_BYTES:
            raise ValueError(f"teams_outgoing_secret must decode to at least {TEAMS_SECRET_MIN_BYTES} bytes")
        return v.strip()

    @field_validator(*NON_BLANK_IDS)
    @classmethod
    def _strip_ids(cls, v: str) -> str:
        return v.strip()

    @field_validator("teams_allowed_aad_ids")
    @classmethod
    def _teams_ids_present(cls, v: str) -> str:
        if not _split_ids(v):
            raise ValueError("teams_allowed_aad_ids needs at least one id")
        return v

    @property
    def teams_outgoing_key(self) -> bytes:
        """HMAC key for Teams outgoing-webhook signatures (the token is base64)."""
        return base64.b64decode(self.teams_outgoing_secret)

    @property
    def teams_allowed_aad_id_set(self) -> FrozenSet[str]:
        """Lower-cased: Entra object ids are GUIDs, compared case-insensitively."""
        return frozenset(x.lower() for x in _split_ids(self.teams_allowed_aad_ids))


@lru_cache
def get_settings() -> Settings:
    return Settings()
