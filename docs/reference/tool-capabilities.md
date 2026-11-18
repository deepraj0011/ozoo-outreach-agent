# Tool capability spike (docs-based)

Method: no trial accounts exist, so this is from official public docs (controller ruling).
Anything not confirmed in docs is marked **UNVERIFIED — confirm on trial account**.
Legend: yes / no / unclear.

## Decision: `instantly`
Mandatory rule: C1, C3, C5, C6 must hold, else switch to Smartlead. Neither tool could be fully confirmed from docs.
Instantly was chosen because its docs confirm the most mandatory items (lead create with `custom_variables`,
sent/reply/bounce/unsubscribe/open webhook events, reply endpoint, per-campaign tracking toggles). Smartlead documents
equivalent events but its docs have conflicting event names and no confirmed signing mechanism either.
Risk: C5 "signing" is UNVERIFIED for both. Fallback plan: Instantly webhook custom header carrying `TOOL_WEBHOOK_SECRET`,
compared in constant time. If trial shows C3 or C6 failing, switch `TOOL_NAME` to `smartlead` (adapter interface isolates this).

## Email tool (Instantly API v2, base https://api.instantly.ai/api/v2; docs https://developer.instantly.ai)
| Cap | Result | Endpoint / note |
|---|---|---|
| C1 add lead + custom vars | yes (docs) | `POST /leads` with `campaign`, `email`, `custom_variables`. UNVERIFIED — confirm on trial account (field limits) |
| C2 update vars mid-sequence | unclear | `PATCH /leads/{id}` with `custom_variables`. UNVERIFIED — confirm on trial account (whether in-flight steps pick up change) |
| C3 pause/resume/remove one lead | unclear | remove: `DELETE /leads/{id}` (yes). Per-lead pause/resume: UNVERIFIED — confirm on trial account (candidates: `POST /leads/update-interest-status`, block list, campaign lead removal). Fallback: delete lead + re-add |
| C4 reply in thread | yes (docs) | `POST /emails/reply` using the `email_id` from the sent/reply webhook. UNVERIFIED — confirm on trial account |
| C5 webhooks | partial | `POST /webhooks`; events `email_sent`, `email_opened`, `email_link_clicked`, `reply_received`, `email_bounced`, `lead_unsubscribed` (https://developer.instantly.ai/guides/webhook-events). Step number in sent: UNVERIFIED. Reply body (`reply_text`, `reply_html`) + `email_id`/lead email: yes. Hard vs soft bounce field: UNVERIFIED. Open IP/UA: UNVERIFIED (likely absent, so bot filter relies on timing). Signing: UNVERIFIED — no HMAC documented; use custom header shared secret |
| C6 custom tracking domain, link tracking off | unclear | campaign `link_tracking` / `open_tracking` flags and custom tracking domain setting. UNVERIFIED — confirm on trial account |

Smartlead (https://api.smartlead.ai) for reference: events `EMAIL_SENT`, `EMAIL_OPEN`, `EMAIL_LINK_CLICK`, `EMAIL_REPLY`, `EMAIL_BOUNCE`, `LEAD_UNSUBSCRIBED` via `POST /api/v1/campaigns/{id}/webhooks`; custom fields on lead add. Signing, per-lead pause: UNVERIFIED.

## Cal.com (https://cal.com/docs/developing/guides/automation/webhooks)
| Cap | Result | Note |
|---|---|---|
| K1 routing form -> event types | unclear | Routing forms exist in Cal.com; routing to event types UNVERIFIED — confirm on trial account |
| K2 webhooks | yes (docs) | BOOKING_CREATED, BOOKING_CANCELLED, BOOKING_NO_SHOW_UPDATED, MEETING_ENDED, FORM_SUBMITTED listed. Signature: header `x-cal-signature-256`, HMAC-SHA256 of raw body with the secret (hex) |
| K3 `metadata[lead]` returned | unclear | docs say payload has a `metadata` object; booking-link `metadata[...]` query pass-through UNVERIFIED — confirm on trial account. Fallback: match by attendee email |

Notes from docs: MEETING_ENDED payload is flat (no `payload` wrapper); other events use `triggerEvent`/`createdAt`/`payload`. `x-cal-webhook-version` header exists.

## WhatsApp Cloud API (https://developers.facebook.com/docs/whatsapp/cloud-api)
| Cap | Result | Note |
|---|---|---|
| W1 send hello_world | yes (docs) | `POST /{ver}/{phone_number_id}/messages` type=template. Needs a real test number — UNVERIFIED — confirm on trial account |
| W2 templates ozoo_intro / ozoo_reminder | action required | create via `POST /{waba_id}/message_templates` or Business Manager; **submit for approval on day one** (approval time is the schedule risk). Not yet submitted: no account |
| W3 webhook verify + signing | yes (docs) | GET `hub.mode/hub.verify_token/hub.challenge` echo challenge; POST header `X-Hub-Signature-256: sha256=<HMAC-SHA256(app secret, raw body)>` |
| W4 error codes | partial | 131026 undeliverable/not on WhatsApp, 131049 marketing limit/ecosystem engagement, 131047 re-engagement (outside 24h), 131050 user stopped marketing messages — from Meta error-codes docs, from memory. UNVERIFIED — confirm on trial account |
| W5 button tap shape | partial | quick-reply tap on a template arrives as `type: "button"`, `button.payload`, `button.text` (interactive replies use `type: "interactive"`). Docs page for it not retrieved — UNVERIFIED — confirm on trial account |

## Go-live checklist
- [ ] Re-capture EVERY fixture in `tests/fixtures/` from real webhooks (all carry `_source`; those marked "constructed" are guesses). Update adapter field mappings after the first real webhook of each type.
- [ ] Confirm C3 (per-lead pause), C5 signing and bounce hard/soft field, C6 link-tracking off.
- [ ] Confirm K3 `metadata.lead` round-trip.
- [ ] Submit and get approval for `ozoo_intro` (buttons `BOOK_DEMO`, `ASK_QUESTION`) and `ozoo_reminder`.
- [ ] Record actual WhatsApp error codes 131026/131049/131047/131050.
