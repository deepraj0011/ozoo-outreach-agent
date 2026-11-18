from __future__ import annotations

import base64
import binascii
import ipaddress
import socket
from functools import lru_cache
from datetime import timedelta
from typing import Dict, FrozenSet, List, Literal, Optional
from urllib.parse import urlsplit

import phonenumbers
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LINK_SECRET_MIN_LEN = 32
INTAKE_KEY_MIN_LEN = 24
TEAMS_SECRET_MIN_BYTES = 16
NON_BLANK_SECRETS = (
    "tool_webhook_secret", "cal_webhook_secret", "teams_outgoing_secret", "wa_app_secret",
    "wa_verify_token",
)
NON_BLANK_IDS = ("teams_tenant_id", "teams_sales_channel_id")
LLMProvider = Literal["anthropic", "openai", "azure_openai", "openai_compatible"]
Mode = Literal["fake", "real"]
# email only: "mailpit" = local SMTP to Mailpit (emailtool/mailpit.py); local-only like "fake"
# "gmail" = a real Gmail mailbox over SMTP + IMAP (emailtool/gmail.py): free demo / pilot sending to
# real inboxes, run from this machine; local-only too (production uses the cold-email tool).
# "mailbox" = our own sender over ordinary SMTP + IMAP mailboxes (emailtool/mailbox.py; MAILBOXES):
# several mailboxes, daily caps with warm-up, IMAP IDLE replies. Allowed in production.
EmailMode = Literal["fake", "real", "mailpit", "gmail", "mailbox"]
LOCAL_ONLY_MODES = frozenset({"fake", "mailpit", "gmail"})
MODE_FIELDS = ("email_mode", "whatsapp_mode", "llm_mode", "alerts_mode", "verify_mode")
# Credentials each service needs only when its mode is "real" (blank is fine in fake mode).
REAL_MODE_REQUIRES: Dict[str, tuple] = {
    "email_mode": ("tool_api_key",),
    "verify_mode": (),  # zerobounce_api_key or reacher_url, by verify_provider (checked in _modes)
    "whatsapp_mode": ("wa_phone_number_id", "wa_access_token"),
    "alerts_mode": ("teams_webhook_url",),
    "llm_mode": ("llm_provider", "llm_classifier_model", "llm_chat_model"),
}
# Worker delays (seconds; defaults are the spec values). Divided by TIME_SCALE (local only).
DELAY_DEFAULTS_S: Dict[str, float] = {
    "click_settle": 30.0,
    "booking_nudge_after": 30 * 60.0,
    "wa_reminder_after": 48 * 3600.0,
    "wa_no_response_after": 48 * 3600.0,
    "end_of_sequence_wait": 5 * 86400.0,
}
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


MAILBOX_PRESETS = {  # provider -> (smtp host, smtp port, imap host, imap port)
    "google": ("smtp.gmail.com", 465, "imap.gmail.com", 993),
    "zoho": ("smtp.zoho.com", 465, "imap.zoho.com", 993),
    "zoho_in": ("smtp.zoho.in", 465, "imap.zoho.in", 993),
    "microsoft": ("smtp.office365.com", 587, "outlook.office365.com", 993),
}


class MailboxConfig(BaseModel):
    """One sending mailbox (EMAIL_MODE=mailbox). MAILBOXES is a JSON list of these. The password is
    an app password (Google / Zoho) or the account password where the provider allows SMTP AUTH."""

    address: str
    password: SecretStr
    provider: Literal["google", "zoho", "zoho_in", "microsoft", "custom"] = "google"
    smtp_host: Optional[str] = None  # custom provider (or to override a preset)
    smtp_port: Optional[int] = Field(None, ge=1, le=65535)
    imap_host: Optional[str] = None
    imap_port: Optional[int] = Field(None, ge=1, le=65535)
    from_name: str = "Ozoo.ai"
    daily_limit: int = Field(30, ge=1, le=2000)  # the most this mailbox sends in a day
    warmup_start: Optional[int] = Field(10, ge=1)  # day-one cap; null: daily_limit from day one
    warmup_step: int = Field(2, ge=0, le=100)  # added per day since the mailbox's first send
    min_gap_s: float = Field(60.0, ge=0, le=3600)  # at least this long between two sends

    @model_validator(mode="after")
    def _hosts(self) -> "MailboxConfig":
        if "@" not in self.address.strip() or not self.password.get_secret_value().strip():
            raise ValueError("each mailbox needs an address and a password")
        preset = MAILBOX_PRESETS.get(self.provider)
        if preset is None and not (self.smtp_host and self.imap_host):
            raise ValueError("a custom mailbox needs smtp_host and imap_host")
        sh, sp, ih, ip = preset or (None, 465, None, 993)
        object.__setattr__(self, "smtp_host", (self.smtp_host or sh).strip())
        object.__setattr__(self, "smtp_port", self.smtp_port or sp)
        object.__setattr__(self, "imap_host", (self.imap_host or ih).strip())
        object.__setattr__(self, "imap_port", self.imap_port or ip)
        return self


class Settings(BaseSettings):
    """Environment-driven settings. Secrets have no defaults and are required.

    Dict fields (link_targets, campaign_ids) are JSON-encoded env vars.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    # Run mode. "local" (one machine, scripts/run_local) defaults every service below to "fake":
    # no real accounts, no network, no cost. "production" allows only "real" (validated).
    app_env: Literal["local", "production"] = "production"
    email_mode: Optional[EmailMode] = None  # fake: in-memory tool; mailpit: local SMTP (emailtool/mailpit.py)
    whatsapp_mode: Optional[Mode] = None  # fake: FakeWhatsAppClient (sender policy still runs)
    llm_mode: Optional[Mode] = None  # fake: deterministic keyword RuleBasedLLM
    alerts_mode: Optional[Mode] = None  # fake: console line + Alert row
    verify_mode: Optional[Mode] = None  # fake: every address valid unless it has "bounce" / "invalid"
    # Local fake clock (python -m outreach.dev advance); read only when app_env is local.
    fake_clock_file: Optional[str] = None
    # APP_ENV=local with EMAIL_MODE / WHATSAPP_MODE real sends to real people: TIME_SCALE, the fake
    # clock and shorter-than-spec delays are refused (and a clock offset ignored) unless this is 1.
    allow_real_local: bool = False
    # Trust X-Forwarded-For for the click IP only behind a proxy you run (default: the socket peer).
    trust_proxy_headers: bool = False

    # Core
    database_url: str
    link_secret: str
    link_base_url: str
    link_targets: Dict[str, str]

    # Email tool
    tool_name: Literal["instantly", "smartlead"]
    tool_api_key: str = ""  # required when email_mode is real
    # EMAIL_MODE=mailpit only: Mailpit's SMTP port on this machine (loopback host enforced) and the
    # tool's state file (default .mailpit_tool.db in the repo).
    smtp_host: str = "127.0.0.1"
    smtp_port: int = Field(1025, ge=1, le=65535)
    mailpit_state_file: Optional[str] = None
    # EMAIL_MODE=gmail only: the Gmail address and its 16-character app password (Google account ->
    # Security -> 2-Step Verification -> App passwords). Sender name shown in the From line.
    gmail_address: str = ""
    gmail_app_password: str = ""
    gmail_from_name: str = "Ozoo.ai"
    # EMAIL_MODE=mailbox: JSON list of mailboxes (see MailboxConfig), e.g.
    # [{"address": "deep@send.ozoo.ai", "password": "app-password", "provider": "google"}]
    mailboxes: List[MailboxConfig] = []
    mailbox_state_file: Optional[str] = None  # default .mailbox_tool.db in the repo
    # mailpit / gmail / mailbox modes (we send the emails ourselves): add our 1x1 open-tracking
    # image. Off by default: opens are unreliable (Apple / Gmail proxies load images by themselves)
    # and a tracking image can lower inbox placement for cold email. Replies are the real signal.
    email_open_pixel: bool = False
    # Same modes: a hidden honeypot link in every email. No reader sees it, so a visit marks the
    # visitor (and its other clicks on that email from the same network) as a security scanner.
    email_honeypot: bool = True
    # Every tracked email link opens a tiny page that confirms the click with a JavaScript beacon
    # before forwarding. Most link scanners fetch without running JavaScript; a click with no beacon
    # is labelled bot. Set false for a plain 302 redirect.
    click_js_confirm: bool = True
    # Trial safety for real leads: while a lead has not replied or booked, a score that turns "hot"
    # from link clicks alone sends a "click_check" alert (check before contacting) instead of the
    # hot_lead sales alert. Bank security sandboxes can click links like a person. Set false once
    # real data shows the click filter holds.
    click_alerts_need_check: bool = True
    # Real-visit tracking (outreach.visits): tracked links to our own site pages (pricing, case
    # study, product, homepage) carry ?oz_v=<signed token>; the site's /v.js snippet reports an
    # engaged visit (15 s of active reading plus scrolling, or a second page). Engaged visits score
    # and count as a confirmed human signal. Needs the snippet on the website.
    email_signature: str = Field("The Ozoo.ai team", max_length=200)  # {signature} in email copy
    # Templates screen (task 22): approving a template, switching one off and sending manually need
    # this passcode (the product manager's). Unset = those actions are refused; drafts still work.
    template_approver_passcode: Optional[SecretStr] = None
    visit_tracking: bool = True
    # Our own sender (mailbox / gmail mode): send only inside this window, follow-ups rotating
    # afternoon / morning (outreach.sendwindow). Unset = Mon-Fri 09:30-17:30 in production, off
    # locally; "" = off. Zone: EMAIL_SEND_TZ, else BUSINESS_TZ (the leads' market).
    email_send_window: Optional[str] = None
    email_send_tz: Optional[str] = None
    tool_webhook_secret: str
    campaign_ids: Dict[str, str]
    default_tz_group: str

    # LLM (reply classifier + WhatsApp chatbot); see .env.example for one block per provider.
    # llm_base_url: required for azure_openai (the resource endpoint) and openai_compatible; optional
    # for anthropic / openai (a gateway). Must be https; plain http is accepted only for
    # openai_compatible on a self-hosted host (localhost, 127.0.0.1, private IP, dot-less name such
    # as a Docker service, *.local / *.internal). Link-local (169.254.0.0/16, fe80::/10) and cloud
    # metadata hosts are always rejected. Never logged.
    llm_provider: Optional[LLMProvider] = None  # required when llm_mode is real
    llm_api_key: str = ""  # secret; may be empty only for openai_compatible (e.g. local Ollama)
    llm_base_url: Optional[str] = None
    azure_openai_api_version: Optional[str] = None
    llm_classifier_model: str = ""  # for azure_openai: the deployment name
    llm_chat_model: str = ""
    llm_timeout_s: float = Field(30.0, gt=0, le=300)

    # Intake
    # Email verification before email 1 (verify_mode real): "zerobounce" (paid API) or "reacher"
    # (open source, self-hosted; REACHER_URL = its backend, REACHER_SECRET = its x-reacher-secret).
    verify_provider: Literal["zerobounce", "reacher"] = "zerobounce"
    zerobounce_api_key: str = ""  # required when verify_mode is real and the provider is zerobounce
    reacher_url: str = ""  # required when verify_mode is real and the provider is reacher
    reacher_secret: str = ""
    intake_api_key: str

    # Cal.com (the booking page is LINK_TARGETS["booking"], required)
    cal_webhook_secret: str
    # Qualifying-form fit (sales lead confirms; the Cal.com routing form uses the same rule):
    # high = size >= fit_high_min_size AND months <= fit_high_max_months;
    # medium = size >= fit_medium_min_size OR months <= fit_medium_max_months; else low.
    fit_high_min_size: int = Field(50, ge=0)
    fit_high_max_months: int = Field(3, ge=0)
    fit_medium_min_size: int = Field(10, ge=0)
    fit_medium_max_months: int = Field(6, ge=0)

    # Microsoft Teams
    # Workflows "When a Teams webhook request is received" URL; its sig= query part is a secret
    teams_webhook_url: str = ""  # required when alerts_mode is real
    # outgoing-webhook security token (base64), HMAC key for POST /teams/commands
    teams_outgoing_secret: str
    teams_tenant_id: str
    teams_sales_channel_id: str
    # comma-separated Entra (AAD) object ids allowed to use the wa / done commands
    teams_allowed_aad_ids: str

    # WhatsApp
    wa_phone_number_id: str = ""  # required when whatsapp_mode is real
    wa_access_token: str = ""  # required when whatsapp_mode is real
    wa_app_secret: str
    wa_verify_token: str
    wa_graph_version: str = "v21.0"

    # Channels and hours
    primary_when_both: Literal["whatsapp", "email"] = "whatsapp"
    business_tz: str = "Asia/Kolkata"
    # Country used to read a phone number written without its country code ("98765 43210") when the
    # lead has no country. Blank: such numbers are rejected. Two-letter ISO code.
    default_phone_region: Optional[str] = "IN"
    business_hours: str = "Mon-Fri 09:00-18:00"

    # Approvals: EU cold email needs legal sign-off; until then EU leads get no email
    eu_email_approved: bool = False

    # Worker (outreach.worker.tick): RUN_WORKER=1 runs it inside the web app every
    # WORKER_INTERVAL_S seconds; or run `python -m outreach.worker --loop` as its own process.
    run_worker: bool = False
    worker_interval_s: float = Field(60.0, gt=0, le=3600)
    worker_batch_size: int = Field(50, ge=1, le=1000)

    # Worker delays, seconds (defaults = spec). Override one, or set TIME_SCALE to divide them all
    # (TIME_SCALE=1440 turns a day into a minute). TIME_SCALE is refused in production.
    time_scale: float = Field(1.0, gt=0, le=100000)
    click_settle_s: float = Field(DELAY_DEFAULTS_S["click_settle"], gt=0)
    booking_nudge_after_s: float = Field(DELAY_DEFAULTS_S["booking_nudge_after"], gt=0)
    wa_reminder_after_s: float = Field(DELAY_DEFAULTS_S["wa_reminder_after"], gt=0)
    wa_no_response_after_s: float = Field(DELAY_DEFAULTS_S["wa_no_response_after"], gt=0)
    end_of_sequence_wait_s: float = Field(DELAY_DEFAULTS_S["end_of_sequence_wait"], gt=0)

    @field_validator(*MODE_FIELDS, "llm_provider", "fake_clock_file", mode="before")
    @classmethod
    def _blank_mode_is_default(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else (v.strip() if isinstance(v, str) else v)

    @field_validator("default_phone_region", mode="before")
    @classmethod
    def _phone_region(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        code = str(v).strip().upper()
        if code not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError("default_phone_region must be a two-letter country code, e.g. IN")
        return code

    @model_validator(mode="after")
    def _modes(self) -> "Settings":
        """Fill unset modes (local: fake, production: real); production refuses any fake mode
        and TIME_SCALE; a real mode needs its credentials. Messages name fields, never values."""
        default = "fake" if self.app_env == "local" else "real"
        for name in MODE_FIELDS:
            if getattr(self, name) is None:
                object.__setattr__(self, name, default)
        if self.app_env == "production":
            fakes = [n for n in MODE_FIELDS if getattr(self, n) in LOCAL_ONLY_MODES]
            if fakes:
                raise ValueError(f"fake / mailpit mode is not allowed in production: {', '.join(fakes)}")
            if self.time_scale != 1.0:
                raise ValueError("time_scale is not allowed in production")
            short = [f"{n}_s" for n, default in DELAY_DEFAULTS_S.items() if getattr(self, f"{n}_s") < default]
            if short:
                raise ValueError(f"shorter-than-spec delays are not allowed in production: {', '.join(short)}")
        if self.email_mode == "mailbox":
            if not self.mailboxes:
                raise ValueError("must not be blank: mailboxes (because email_mode is mailbox)")
            addrs = [m.address.strip().lower() for m in self.mailboxes]
            if len(set(addrs)) != len(addrs):
                raise ValueError("mailboxes: each address may be listed once")
        if self.email_mode == "gmail" and (not self.gmail_address.strip() or not self.gmail_app_password.strip()):
            raise ValueError("must not be blank: gmail_address, gmail_app_password (because email_mode is gmail)")
        if (self.app_env == "local" and self.real_sending and not self.allow_real_local):
            bad = []
            if self.time_scale != 1.0:
                bad.append("time_scale")
            if self.fake_clock_file:
                bad.append("fake_clock_file")
            bad += [f"{n}_s" for n, default in DELAY_DEFAULTS_S.items() if getattr(self, f"{n}_s") < default]
            if bad:
                raise ValueError("real email / WhatsApp sending in local mode refuses "
                                 f"{', '.join(bad)} (real people would get messages early); "
                                 "set ALLOW_REAL_LOCAL=1 to override")
        missing = [f"{f} (because {mode} is real)" for mode, fields in REAL_MODE_REQUIRES.items()
                   if getattr(self, mode) == "real" for f in fields
                   if getattr(self, f) is None or (isinstance(getattr(self, f), str) and not getattr(self, f).strip())]
        if self.verify_mode == "real":
            need = "reacher_url" if self.verify_provider == "reacher" else "zerobounce_api_key"
            if not getattr(self, need).strip():
                missing.append(f"{need} (because verify_mode is real, provider {self.verify_provider})")
        if missing:
            raise ValueError(f"must not be blank: {', '.join(missing)}")
        return self

    @property
    def real_sending(self) -> bool:
        """Email or WhatsApp goes to real people (a real provider)."""
        return "real" in (self.email_mode, self.whatsapp_mode) or self.email_mode in ("gmail", "mailbox")

    @field_validator("smtp_host")
    @classmethod
    def _smtp_loopback(cls, v: str) -> str:
        """Mailpit mode must never relay to a real mail server."""
        from .emailtool.mailpit import is_loopback_host

        if not is_loopback_host(v):
            raise ValueError("smtp_host must be a loopback address (127.0.0.1, localhost or ::1)")
        return v.strip()

    @model_validator(mode="after")
    def _links(self) -> "Settings":
        """The booking target is required (never a silent fall back to the homepage); in production
        LINK_BASE_URL and every link target must be https. Messages name keys, never URLs."""
        if not (self.link_targets.get("booking") or "").strip():
            raise ValueError('link_targets must contain "booking" (the Cal.com booking page)')
        if self.app_env == "production":
            urls = {"link_base_url": self.link_base_url,
                    **{f"link_targets[{k}]": v for k, v in self.link_targets.items()}}
            bad = [name for name, url in urls.items() if urlsplit(str(url).strip()).scheme != "https"]
            if bad:
                raise ValueError(f"must be https in production: {', '.join(sorted(bad))}")
        return self

    def delay(self, name: str) -> timedelta:
        """A worker delay (see DELAY_DEFAULTS_S) after TIME_SCALE."""
        return timedelta(seconds=float(getattr(self, f"{name}_s")) / self.time_scale)

    @field_validator("business_tz")
    @classmethod
    def _business_tz_known(cls, v: str) -> str:
        from .hours import business_zone

        business_zone(v.strip())
        return v.strip()

    @field_validator("email_send_window")
    @classmethod
    def _send_window_valid(cls, v: Optional[str]) -> Optional[str]:
        from .hours import parse_business_hours

        if v is not None and v.strip():
            parse_business_hours(v)
        return v

    @field_validator("email_send_tz")
    @classmethod
    def _send_tz_known(cls, v: Optional[str]) -> Optional[str]:
        from .hours import business_zone

        if v is not None and v.strip():
            business_zone(v.strip())
        return v.strip() if v else None

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
        if not v.strip():
            return ""  # blank: only valid when alerts_mode is fake (checked in _modes)
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

    @field_validator("llm_classifier_model", "llm_chat_model", "tool_api_key", "zerobounce_api_key",
                     "wa_phone_number_id", "wa_access_token")
    @classmethod
    def _strip_mode_fields(cls, v: str) -> str:
        # blank is checked in _modes (required only when the service's mode is real)
        return v.strip() if not v.strip() else v

    @field_validator("llm_base_url", "azure_openai_api_version", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else (v.strip() if isinstance(v, str) else v)

    @model_validator(mode="after")
    def _llm_settings(self) -> "Settings":
        # messages name the field only: never echo the key or URL
        provider = self.llm_provider
        if provider is None:
            return self  # llm_mode fake (a real llm_mode without a provider fails in _modes)
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
