"""Connector status for the web app's Connectors page (pure: settings + DB reads, no I/O).

Status rules:
- Email tools: the one matching settings.email_mode (gmail -> gmail, mailbox -> mailboxes, real with
  tool_name == "instantly" -> instantly) is "active"; the others "available". In fake / mailpit mode no
  email card is active and the first card's detail says "Local test mode: emails go to Mailpit or memory".
- The active email tool is "attention" when an Alert of kind mailbox_paused / mailbox_unhealthy is
  unresolved and was created in the last 24 h.
- cal: "active" once a Cal.com event exists (detail: last booking date), else "available".
- google_calendar, outlook_calendar: always "coming_soon".
- whatsapp, teams, llm, verify: "active" when their mode is real, "available" otherwise (fake: the
  detail says "Local test mode"). teams is "attention" when a non-integration_error Alert has stayed
  unposted for 10 minutes or more.

No secret value (password, token, API key, webhook URL, secret, phone number id) is ever put in the
output: `account` holds only non-secret identity, `env_keys` only variable names.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from outreach.config import Settings
from outreach.models import Alert, Event

MAILBOX_ALERT_KINDS = ("mailbox_paused", "mailbox_unhealthy")
MAILBOX_ALERT_WINDOW = timedelta(hours=24)
TEAMS_STUCK_AFTER = timedelta(minutes=10)
LOCAL_EMAIL_DETAIL = "Local test mode: emails go to Mailpit or memory"
LOCAL_DETAIL = "Local test mode"
SETTINGS_ONLY = "Based on settings only"
CALENDAR_STEPS = ["In-app sign-in is coming in the next update."]


def _card(id_: str, group: str, name: str, account: str, status: str, detail: str, basis: str,
          steps: Iterable[str], env_keys: Iterable[str]) -> Dict[str, Any]:
    return {"id": id_, "group": group, "name": name, "account": account, "status": status,
            "detail": detail, "basis": basis,
            "setup": {"steps": list(steps), "env_keys": list(env_keys)}}


def _recent_alerts(session: Session, kinds: Iterable[str], since: datetime) -> int:
    return session.scalar(select(func.count()).select_from(Alert).where(
        Alert.kind.in_(list(kinds)), Alert.resolved_at.is_(None), Alert.created_at >= since)) or 0


def _stuck_alerts(session: Session, cutoff: datetime) -> int:
    return session.scalar(select(func.count()).select_from(Alert).where(
        Alert.kind != "integration_error", Alert.posted_at.is_(None),
        Alert.created_at <= cutoff)) or 0


def _last_cal_event(session: Session) -> Optional[datetime]:
    return session.scalar(select(func.max(Event.received_at)).where(Event.channel == "cal"))


def _active_email_id(settings: Settings) -> Optional[str]:
    if settings.email_mode == "gmail":
        return "gmail"
    if settings.email_mode == "mailbox":
        return "mailboxes"
    if settings.email_mode == "real" and settings.tool_name == "instantly":
        return "instantly"
    return None


def _email_state(card_id: str, settings: Settings, session: Session, now: datetime,
                 active_detail: str, idle_detail: str) -> Dict[str, str]:
    if _active_email_id(settings) != card_id:
        return {"status": "available", "detail": idle_detail, "basis": SETTINGS_ONLY}
    if _recent_alerts(session, MAILBOX_ALERT_KINDS, now - MAILBOX_ALERT_WINDOW):
        return {"status": "attention", "basis": "Mailbox alert in the last 24 h",
                "detail": "A mailbox is paused or unhealthy. Check the alerts."}
    return {"status": "active", "detail": active_detail, "basis": "No mailbox alerts in the last 24 h"}


def _gmail(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    state = _email_state("gmail", settings, session, now, "Sending through your Gmail account",
                         "Not in use. Good for a demo or pilot.")
    if settings.email_mode in ("fake", "mailpit"):
        state = {"status": "available", "detail": LOCAL_EMAIL_DETAIL, "basis": SETTINGS_ONLY}
    account = settings.gmail_address if settings.email_mode == "gmail" else ""
    return _card("gmail", "email", "Gmail", account, steps=[
        "Turn on 2-step verification for the Gmail account.",
        "Create a 16-character app password in the Google account.",
        "Set the variables, set EMAIL_MODE=gmail and restart."],
        env_keys=["EMAIL_MODE", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD"], **state)


def _mailboxes(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    count = len(settings.mailboxes)
    account = "%d mailbox%s" % (count, "" if count == 1 else "es") if count else ""
    state = _email_state("mailboxes", settings, session, now, "Sending through your own mailboxes",
                         "Not in use. Several mailboxes with daily caps.")
    return _card("mailboxes", "email", "Own mailboxes", account, steps=[
        "Create an app password for each sending mailbox.",
        "List the mailboxes as JSON in MAILBOXES, set EMAIL_MODE=mailbox and restart."],
        env_keys=["EMAIL_MODE", "MAILBOXES"], **state)


def _instantly(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    state = _email_state("instantly", settings, session, now, "Sending through Instantly",
                         "Not in use. A cold-email tool.")
    return _card("instantly", "email", "Instantly", "", steps=[
        "Create an API key in Instantly.",
        "Set TOOL_NAME=instantly, the API key, EMAIL_MODE=real and restart."],
        env_keys=["EMAIL_MODE", "TOOL_NAME", "TOOL_API_KEY"], **state)


def _cal(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    last = _last_cal_event(session)
    if last is None:
        status, detail, basis = "available", "No booking received yet", "No Cal.com event stored"
    else:
        status, basis = "active", "Latest Cal.com event stored"
        detail = "Last booking %d %s" % (last.day, last.strftime("%b"))
    return _card("cal", "calendar", "Cal.com", "", status, detail, basis, steps=[
        "In Cal.com add a webhook to this app's Cal.com webhook address.",
        "Use the same secret in CAL_WEBHOOK_SECRET."], env_keys=["CAL_WEBHOOK_SECRET"])


def _coming_soon(id_: str, name: str) -> Dict[str, Any]:
    return _card(id_, "calendar", name, "", "coming_soon", "Not available yet", SETTINGS_ONLY,
                 CALENDAR_STEPS, [])


def _mode_status(mode: Optional[str]) -> Dict[str, str]:
    if mode == "real":
        return {"status": "active", "detail": "Connected (settings)", "basis": SETTINGS_ONLY}
    detail = LOCAL_DETAIL if mode == "fake" else "Not connected"
    return {"status": "available", "detail": detail, "basis": SETTINGS_ONLY}


def _whatsapp(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    account = "Phone number set" if settings.wa_phone_number_id else ""
    return _card("whatsapp", "messaging", "WhatsApp", account, steps=[
        "Create the WhatsApp Business app and phone number in Meta.",
        "Set the phone number id, access token and verify token, set WHATSAPP_MODE=real and restart."],
        env_keys=["WHATSAPP_MODE", "WA_PHONE_NUMBER_ID", "WA_ACCESS_TOKEN", "WA_VERIFY_TOKEN"],
        **_mode_status(settings.whatsapp_mode))


def _teams(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    state = _mode_status(settings.alerts_mode)
    if settings.alerts_mode == "real" and _stuck_alerts(session, now - TEAMS_STUCK_AFTER):
        state = {"status": "attention", "detail": "Some alerts did not reach Teams",
                 "basis": "Unposted alerts older than 10 min"}
    account = "Sales channel set" if settings.teams_sales_channel_id else ""
    return _card("teams", "messaging", "Microsoft Teams", account, steps=[
        "Add an incoming webhook to the sales channel in Teams.",
        "Set TEAMS_WEBHOOK_URL, set ALERTS_MODE=real and restart."],
        env_keys=["ALERTS_MODE", "TEAMS_WEBHOOK_URL"], **state)


def _llm(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    account = settings.llm_provider or ""
    keys = ["LLM_MODE", "LLM_PROVIDER", "LLM_CLASSIFIER_MODEL", "LLM_CHAT_MODEL", "LLM_API_KEY"]
    if settings.llm_provider in ("azure_openai", "openai_compatible"):
        keys.append("LLM_BASE_URL")
    if settings.llm_provider == "azure_openai":
        keys.append("AZURE_OPENAI_API_VERSION")
    return _card("llm", "messaging", "Language model", account, steps=[
        "Choose the provider and the two models, and add its key.",
        "Set LLM_MODE=real and restart."],
        env_keys=keys,
        **_mode_status(settings.llm_mode))


def _verify(settings: Settings, session: Session, now: datetime) -> Dict[str, Any]:
    keys = ["VERIFY_MODE", "VERIFY_PROVIDER",
            "REACHER_URL" if settings.verify_provider == "reacher" else "ZEROBOUNCE_API_KEY"]
    return _card("verify", "messaging", "Email verification", settings.verify_provider, steps=[
        "Pick ZeroBounce (paid API) or a self-hosted Reacher.",
        "Set the provider's key or URL, set VERIFY_MODE=real and restart."],
        env_keys=keys, **_mode_status(settings.verify_mode))


def connectors(settings: Settings, session: Session, now: datetime) -> List[Dict[str, Any]]:
    """The ten connector cards, in fixed order."""
    return [_gmail(settings, session, now), _mailboxes(settings, session, now),
            _instantly(settings, session, now), _cal(settings, session, now),
            _coming_soon("google_calendar", "Google Calendar"),
            _coming_soon("outlook_calendar", "Outlook Calendar"),
            _whatsapp(settings, session, now), _teams(settings, session, now),
            _llm(settings, session, now), _verify(settings, session, now)]
