# Testing the outreach agent on your own machine

This guide runs the whole agent on one computer. By default every outside service is **fake**:
no real accounts, no network calls and no cost. You play the outside world (the lead, Instantly,
Meta, Cal.com and Teams) with a small command that sends the same signed webhooks those services
would send.

All commands run from the repo folder. `P` below is short for `.venv/bin/python`.

```bash
P=.venv/bin/python
```

---

## 1. Setup (once)

You need Python 3.10+ and the virtualenv:

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/local_setup
```

`scripts/local_setup`:

- writes `.env.local` with local defaults and new random secrets (`APP_ENV=local`, every service fake);
- creates the SQLite database `local.db`;
- installs nothing.

You can run it again safely: it keeps an existing `.env.local`. Add `--force` to make a new one
with new secrets. Never commit `.env.local` (git already ignores it).

## 2. Start

```bash
scripts/run_local
```

This starts the app on <http://127.0.0.1:8000>, with the worker running inside it every 10 seconds
(`RUN_WORKER=1`). Leave this terminal open: it shows the log, and every alert appears there as one
line, like this:

```
[ALERT wa_human_needed] lead 2: lead 2: WhatsApp chat needs a person (reason: wants_human). The bot is paused.
```

Stop it with **Ctrl+C**.

- **Run only one process that writes the database.** The worker already runs inside the app. Do
  not also start `python -m outreach.worker --loop`. The short `dev` commands below are fine.
- **You can start the app from any folder.** Templates, the knowledge base and
  `config/scanners.yaml` are found from the repo itself, not from the current folder.

Open a **second terminal** in the repo folder for everything below.

## 3. Seed sample leads

```bash
$P -m outreach.dev seed
```

This adds five leads through the real intake code:

| id | Lead | Starts on |
|---|---|---|
| 1 | email only (`ana@acme.example.com`) | email |
| 2 | WhatsApp only, opted in (`+12015550101`) | WhatsApp |
| 3 | both channels, opted in | WhatsApp (`PRIMARY_WHEN_BOTH=whatsapp`) |
| 4 | EU, email only | held: `paused` until `EU_EMAIL_APPROVED=true` |
| 5 | an email that will bounce | email |

Within 10 seconds the worker starts them: it enrols the email leads in the fake email tool and
sends the fake WhatsApp intro template. To run it right away instead:

```bash
$P -m outreach.dev tick
```

The ids above assume a fresh database. Delete `local.db` and run `scripts/local_setup` again to
start over.

## Useful commands

| Command | What it does |
|---|---|
| `$P -m outreach.dev show` | all leads: state, channel, score |
| `$P -m outreach.dev show 2` | one lead: state, flags, events, chat, jobs and alerts |
| `$P -m outreach.dev show --mask` | the same, with emails and phones hidden |
| `$P -m outreach.dev tick` | run one worker tick now |
| `$P -m outreach.dev advance --hours 48` | move the fake clock 48 hours ahead |
| `$P -m outreach.dev advance --reset` | go back to real time |
| `$P -m outreach.dev sim SCENARIO --lead ID ...` | send a signed webhook; prints the HTTP status, then the lead |

**The fake clock.** `advance` moves time forward for the app, its worker and the `dev`
commands together, so you can test "48 hours later" straight away. After `advance`, wait up to
10 seconds or run `tick`.

**Speeding up real time instead.** Put `TIME_SCALE=1440` in `.env.local` (one day becomes one
minute), or set single delays in seconds: `WA_REMINDER_AFTER_S`, `WA_NO_RESPONSE_AFTER_S`,
`END_OF_SEQUENCE_WAIT_S`, `CLICK_SETTLE_S` and `BOOKING_NUDGE_AFTER_S`. Then restart
`run_local`. Production refuses `TIME_SCALE`.

**Email timing.** In fake mode nothing sends Email 2 to 4 for you. The real email tool runs that
schedule, so you announce each send yourself with `sim email-sent --step N`.

## 4. Scenarios

Each `sim` prints the HTTP status, then the lead's state. Use `show ID` for the details.

### Email only (lead 1)

| Try | Command | You should see |
|---|---|---|
| Email 1 went out | `sim email-sent --lead 1 --step 1` | `step_sent=1`. It is dated 10 minutes ago, so a click right after is not inside the 60-second bot window. |
| Open (logged only) | `sim email-open --lead 1` | an `open` event marked `human`; `human_opens=1`; the score does not change |
| Apple Mail open | `sim email-open --lead 1 --ip 17.58.0.1` | an `open` event marked `machine` |
| Pricing click | `sim click --lead 1 --tag pricing`, then `tick` after 30 seconds (or `advance --hours 0.01` and `tick`) | `HTTP 302`; after the tick: score 10 and a `[ALERT hot_lead]` line |
| Scanner click | `sim click --lead 1 --tag homepage --scanner`, then `tick` | the click is marked `bot`; no points |
| Interested reply | `sim email-reply --lead 1 --text "Sounds interesting, can we talk?"` | `qualifying`; `show 1` lists a `classify` job marked `done` |
| Later | `sim email-reply --lead 1 --text "Not now, try in March"` | `nurture`, `nurture_until` on 1 March |
| Out of office | `sim email-reply --lead 1 --text "Out of office until 2026-12-01"` | `paused` (`ooo`). After `advance` past that date and a `tick`: `in_sequence` |
| Unsubscribe link | `sim click --lead 1 --tag unsubscribe` | `HTTP 302 -> /u/...`. Nothing changes yet: a scanner only GETs. |
| Confirm unsubscribe | `sim unsub-link --lead 1` | `suppressed`; the email and phone are both suppressed |
| Tool unsubscribe | `sim email-unsub --lead 1` | `suppressed` |
| Hard bounce | `sim email-bounce --lead 5 --hard` | `suppressed` |
| End of sequence | `sim email-sent --lead 1 --step 4`, then `advance --hours 121` and `tick` | `nurture`, with route `monthly`, `warm_2_3w` or `sales_personal` |

Each lead can only take one path, so re-seed (or try another lead) to test the next one.

### WhatsApp only (lead 2)

| Try | Command | You should see |
|---|---|---|
| Book demo tap | `sim wa-button --lead 2 --id book_demo` | `qualifying`; `show 2` lists the booking-link message |
| Then books | `sim cal-booked --lead 2` | `booked`, plus a `[ALERT booked]` line |
| Ask a question | `sim wa-button --lead 2 --id ask_question`, then `sim wa-text --lead 2 --text "Which languages do you support?"` | the bot answers from `kb/ozoo_kb.md` (see the note below) |
| Price question | `sim wa-text --lead 2 --text "What would a custom price be?"` | a `[ALERT wa_human_needed]` line; the bot stops |
| Salesperson replies from Teams | `sim teams --text "wa 2 Hi, Raj here"` | `teams reply: Sending to lead 2…`; `show 2` lists a `human` message |
| Give the chat back | `sim teams --text "done 2"` | `Lead 2 is back with the bot.` |
| STOP | `sim wa-text --lead 2 --text STOP` | `suppressed`; nothing more is sent, even after `advance` and `tick` |
| No response | after the seed: `advance --hours 48`, `tick`, `advance --hours 48`, `tick` | the `ozoo_reminder` template, then `nurture` |
| Delivery status | `sim wa-status --lead 2 --status delivered` | a `wa_delivered` event (it uses the lead's last outgoing message) |

**Knowledge base.** The fake AI answers by matching words against `kb/ozoo_kb.md`. That file is
still a placeholder, so most questions go to a person until it has real lines. Add one line, for
example `- Ozoo.ai works in English and Hindi.`, and ask about it. Any question about price, cost
or a quote, or containing the word "human", always goes to a person.

### Both channels (lead 3)

| Try | Command | You should see |
|---|---|---|
| No WhatsApp response | `advance --hours 48`, `tick`, `advance --hours 48`, `tick` | reminder, then `channel=email (switched)`; the email tool enrols it; no more WhatsApp messages |
| Number not on WhatsApp | (fresh lead 3) `sim wa-status --lead 3 --status failed --code 131026` | `channel=email (switched)`; email starts |
| WhatsApp tap after the switch | `sim wa-button --lead 3 --id book_demo` | `qualifying`; the log shows `fake email tool: stop recorded` |
| Meeting held | `sim cal-booked --lead 3`, `sim cal-form --lead 3 --size 51-200 --timeline now`, `sim cal-meeting-ended --lead 3` | `handed_off`, plus a `[ALERT handoff]` line (`show 3` lists both channels) |
| Cancelled / rescheduled / no-show | `sim cal-cancelled` / `cal-rescheduled` / `cal-no-show --lead 3` | the matching state and alert |

### Any lead

| Try | What to do | You should see |
|---|---|---|
| Same webhook twice | Run the same `sim` twice. To resend the exact same bytes, use the e2e test (`test_duplicate_webhook_has_no_duplicate_effect`). | Each `sim` call is a new event. The exact same webhook sent twice is stored once and acted on once. |
| Bad signature | Change a secret in `.env.local` only for the CLI, e.g. `WA_APP_SECRET=wrong $P -m outreach.dev sim wa-text --lead 2 --text hi` | `HTTP 401` |

**The automated version.** `.venv/bin/pytest tests/test_local_e2e.py` runs every row of the plan's
e2e table this way, in about 3 seconds.

## 5. Switching one service to real

Edit `.env.local`: set that service's mode to `real` and fill in its settings, then restart
`run_local`. The other services stay fake. When a service is set to `real`, the app refuses to
start if its credentials are missing.

| Service | Set | Also |
|---|---|---|
| **Email (Instantly)** | `EMAIL_MODE=real`, `TOOL_API_KEY`, `CAMPAIGN_IDS` (your test campaign, e.g. `'{"default":"<campaign id>"}'`), `DEFAULT_TZ_GROUP=default` | In Instantly, point the webhook at `<tunnel>/webhooks/email` with the header `X-Ozoo-Webhook-Secret: <TOOL_WEBHOOK_SECRET>`, and turn link tracking off. Set short step delays in the campaign. |
| **Email verification (ZeroBounce)** | `VERIFY_MODE=real`, `ZEROBOUNCE_API_KEY` | uses credits |
| **WhatsApp (Meta test number)** | `WHATSAPP_MODE=real`, `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, and `WA_APP_SECRET` = your Meta app secret (replace the generated one) | In Meta, set the webhook to `<tunnel>/webhooks/whatsapp` with verify token `WA_VERIFY_TOKEN`, and subscribe to `messages`. The `ozoo_intro` and `ozoo_reminder` templates must be approved. |
| **Cal.com** | `CAL_WEBHOOK_SECRET` = the Cal.com webhook secret, `CAL_BOOKING_URL`, and `"booking"` in `LINK_TARGETS` = your Cal.com event link | Cal.com webhook → `<tunnel>/webhooks/cal`. Cal.com has no mode: it only sends to us. |
| **Teams alerts** | `ALERTS_MODE=real`, `TEAMS_WEBHOOK_URL` (Workflows "When a Teams webhook request is received" URL) | |
| **Teams commands** | `TEAMS_OUTGOING_SECRET` (the outgoing-webhook token), `TEAMS_TENANT_ID`, `TEAMS_SALES_CHANNEL_ID`, `TEAMS_ALLOWED_AAD_IDS` | outgoing webhook callback → `<tunnel>/teams/commands` |
| **LLM: Anthropic** | `LLM_MODE=real`, `LLM_PROVIDER=anthropic`, `LLM_API_KEY`, `LLM_CLASSIFIER_MODEL`, `LLM_CHAT_MODEL` | |
| **LLM: OpenAI** | `LLM_PROVIDER=openai` + key + models | |
| **LLM: Azure OpenAI** | `LLM_PROVIDER=azure_openai`, key, `LLM_BASE_URL=https://<resource>.openai.azure.com`, `AZURE_OPENAI_API_VERSION`, models = deployment names | |
| **LLM: hosted OpenAI-compatible** (Gemini, Mistral, Groq, OpenRouter) | `LLM_PROVIDER=openai_compatible`, key, `LLM_BASE_URL` (https), models | |
| **LLM: local Ollama (free)** | see below | |

Local Ollama example (after `ollama pull llama3.1`):

```
LLM_MODE=real
LLM_PROVIDER=openai_compatible
LLM_API_KEY=
LLM_BASE_URL=http://localhost:11434/v1
LLM_CLASSIFIER_MODEL=llama3.1
LLM_CHAT_MODEL=llama3.1
```

The `.env.example` file has one commented block per provider.

## 6. Receiving real webhooks

Real providers cannot reach `127.0.0.1`. Open a tunnel:

```bash
cloudflared tunnel --url http://127.0.0.1:8000
# or
ngrok http 8000
```

Then:

1. Set `LINK_BASE_URL=https://<your-tunnel-host>` in `.env.local`, so tracked links in real emails and
   WhatsApp messages point to the tunnel.
2. Restart `run_local`.
3. Set each provider's webhook URL to `https://<your-tunnel-host>/webhooks/email`, `/webhooks/whatsapp`,
   `/webhooks/cal` or `/teams/commands`.

A quick tunnel's address changes every time it starts, so update the URLs each time.

## 7. Safety notes

- **Real WhatsApp:** send only to phone numbers registered as test recipients in Meta's test
  mode. Never use real leads' numbers locally.
- **Real email:** send only to inboxes you own. Keep seed and intake addresses on your own domain
  or on `example.com`, which never receives mail.
- **Real LLM:** the lead's reply text and chat messages go to that provider. Use made-up text.
- `.env.local` holds secrets: never commit it or share it. `--force` makes new ones.
- Production can never run fake: the settings refuse any `*_MODE=fake` or `TIME_SCALE` unless
  `APP_ENV=local`. The `dev` CLI also refuses to run unless `APP_ENV=local`.
- Logs never contain emails, phone numbers or message text, even locally. Only `dev show` prints
  them, and `--mask` hides them.

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| `could not reach http://127.0.0.1:8000` | Start `scripts/run_local`, or point `--base-url` / `DEV_BASE_URL` at your port. |
| `settings invalid (...)` / `refusing to run` | Run `scripts/local_setup`, and check that `.env.local` has `APP_ENV=local`. |
| The app will not start: `must not be blank: ... (because X_MODE is real)` | Fill in that service's settings, or set its mode back to `fake`. |
| `HTTP 401` from `sim` | The CLI and the server are using different secrets. Restart `run_local` after you edit `.env.local`, or after `local_setup --force`. |
| A click is marked `bot` | The click came within 60 seconds of the email send. `email-sent` is dated 10 minutes ago by default (`--minutes-ago`). |
| Nothing happens after `advance` | Run `tick`, or wait 10 seconds for the server's worker. |
| `database is locked` | Something else is writing `local.db`. Stop any extra `worker --loop`. |
| `lead N has no phone` / `has no email` | That scenario needs that contact. Use another lead. |
| Start fresh | Stop the app, then `rm local.db .local_clock.json`, `scripts/local_setup` and `seed`. |
