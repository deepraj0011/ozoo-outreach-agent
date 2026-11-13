from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
from functools import lru_cache
from typing import Dict, FrozenSet, Literal, Optional
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LINK_SECRET_MIN_LEN = 32
INTAKE_KEY_MIN_LEN = 24
TEAMS_SECRET_MIN_BYTES = 16
NON_BLANK_SECRETS = (
    "tool_webhook_secret", "cal_webhook_secret", "teams_outgoing_secret", "wa_app_secret",
    "wa_verify_token", "tool_api_key", "zerobounce_api_key",
    "wa_access_token",
)
NON_BLANK_IDS = ("teams_tenant_id", "teams_sales_channel_id")
LLMProvider = Literal["anthropic", "openai", "azure_openai", "openai_compatible"]
# A dot-less host name ("ollama") or one of these suffixes never resolves on the public internet.
PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _split_ids(raw: str) -> FrozenSet[str]:
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


# Cloud instance-metadata endpoints (AWS IPv6 is a ULA, not link-local, so it is listed).
METADATA_HOSTS = frozenset({"metadata", "metadata.google.internal", "169.254.169.254", "fd00:ec2::254"})


def _host_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    """The IP a host literal means, including inet_aton forms ("2852039166", "0xa9.0xfe.0xa9.0xfe")
    and IPv4-mapped IPv6; None for a name."""
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
        except (OSError, ValueError):
            return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _forbidden_host(host: str) -> bool:
    """Metadata endpoints and link-local addresses (169.254.0.0/16, fe80::/10): never, for any
    provider or scheme."""
    host = host.lower().rstrip(".")
    ip = _host_ip(host)
    if host in METADATA_HOSTS or (ip is not None and str(ip) in METADATA_HOSTS):
        return True
    return ip is not None and ip.is_link_local


def _private_host(host: str) -> bool:
    """localhost, a loopback / private IP, a dot-less name or a private suffix. Link-local and
    metadata hosts are rejected before this is asked."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(PRIVATE_HOST_SUFFIXES):
        return True
    ip = _host_ip(host)
    if ip is None:
        return "." not in host
    return ip.is_loopback or ip.is_private


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

    # LLM (reply classifier + WhatsApp chatbot); see .env.example for one block per provider.
    # llm_base_url: required for azure_openai (the resource endpoint) and openai_compatible; optional
    # for anthropic / openai (a gateway). Must be https; plain http is accepted only for
    # openai_compatible on a self-hosted host (localhost, 127.0.0.1, private IP, dot-less name such
    # as a Docker service, *.local / *.internal). Link-local (169.254.0.0/16, fe80::/10) and cloud
    # metadata hosts are always rejected. Never logged.
    llm_provider: LLMProvider
    llm_api_key: str = ""  # secret; may be empty only for openai_compatible (e.g. local Ollama)
    llm_base_url: Optional[str] = None
    azure_openai_api_version: Optional[str] = None
    llm_classifier_model: str  # for azure_openai: the deployment name
    llm_chat_model: str
    llm_timeout_s: float = Field(30.0, gt=0, le=300)

    # Intake
    zerobounce_api_key: str
    intake_api_key: str

    # Cal.com
    cal_webhook_secret: str
    cal_booking_url: str
    # Qualifying-form fit (sales lead confirms; the Cal.com routing form uses the same rule):
    # high = size >= fit_high_min_size AND months <= fit_high_max_months;
    # medium = size >= fit_medium_min_size OR months <= fit_medium_max_months; else low.
    fit_high_min_size: int = Field(50, ge=0)
    fit_high_max_months: int = Field(3, ge=0)
    fit_medium_min_size: int = Field(10, ge=0)
    fit_medium_max_months: int = Field(6, ge=0)

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

    # Worker (outreach.worker.tick): RUN_WORKER=1 runs it inside the web app every
    # WORKER_INTERVAL_S seconds; or run `python -m outreach.worker --loop` as its own process.
    run_worker: bool = False
    worker_interval_s: float = Field(60.0, gt=0, le=3600)
    worker_batch_size: int = Field(50, ge=1, le=1000)

    @field_validator("business_tz")
    @classmethod
    def _business_tz_known(cls, v: str) -> str:
        from .hours import business_zone

        business_zone(v.strip())
        return v.strip()

    @field_validator("business_hours")
    @classmethod
    def _business_hours_valid(cls, v: str) -> str:
        from .hours import parse_business_hours

        parse_business_hours(v)
        return v.strip()

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

    @field_validator("llm_classifier_model", "llm_chat_model")
    @classmethod
    def _model_not_blank(cls, v: str, info) -> str:
        if not v.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return v.strip()

    @field_validator("llm_base_url", "azure_openai_api_version", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else (v.strip() if isinstance(v, str) else v)

    @model_validator(mode="after")
    def _llm_settings(self) -> "Settings":
        # messages name the field only: never echo the key or URL
        provider = self.llm_provider
        if provider != "openai_compatible" and not self.llm_api_key.strip():
            raise ValueError(f"llm_api_key must not be blank for llm_provider {provider}")
        if provider in ("azure_openai", "openai_compatible") and not self.llm_base_url:
            raise ValueError(f"llm_base_url is required for llm_provider {provider}")
        if provider == "azure_openai" and not self.azure_openai_api_version:
            raise ValueError("azure_openai_api_version is required for llm_provider azure_openai")
        if self.llm_base_url is not None:
            url = self.llm_base_url
            try:
                parts = urlsplit(url)
                host = parts.hostname
                parts.port  # noqa: B018 - raises on a bad port
            except ValueError:
                parts, host = None, None
            ok = parts is not None and bool(host) and not any(c.isspace() for c in url) and (
                not _forbidden_host(host)) and (
                parts.scheme == "https"
                or (parts.scheme == "http" and provider == "openai_compatible" and _private_host(host)))
            if not ok:
                raise ValueError("llm_base_url must be an https URL, not link-local or a metadata endpoint "
                                 "(plain http only for a self-hosted openai_compatible host: localhost, "
                                 "private IP or internal name)")
        return self

    @model_validator(mode="after")
    def _fit_thresholds_ordered(self) -> "Settings":
        if self.fit_high_min_size < self.fit_medium_min_size:
            raise ValueError("fit_high_min_size must be >= fit_medium_min_size")
        if self.fit_high_max_months > self.fit_medium_max_months:
            raise ValueError("fit_high_max_months must be <= fit_medium_max_months")
        return self

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
