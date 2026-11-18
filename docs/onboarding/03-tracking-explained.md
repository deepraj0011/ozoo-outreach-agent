# 3. Does email tracking actually work? (opens, clicks, unsubscribes, bots)

> **The concern:** "Emails can't really be tracked. Gmail and Outlook have bots that open the email
> and click the links to check them."
>
> **Short answer:** This is **true for opens** and **partly true for clicks**. That's why this agent
> makes its decisions on the signals bots can't fake: replies, bookings, bounces, unsubscribe button
> presses and, new, a **verified page visit** (a person actually read the page the email linked to).
> Opens are switched off by default and score 0 points. Clicks are counted only after a bot filter
> checks them; on their own they never alert sales or email the lead. The filter's known gaps are
> listed below, and a realistic simulation of 100 leads shows what happens in practice (section 4).

---

## 1. What can and can't be trusted

| Signal | Trust it? | Why |
|---|---|---|
| **Reply** | ✅ Yes | Bots don't write replies. Auto-replies (out of office and similar) are recognised by the AI reply reader and handled separately. |
| **Bounce** | ✅ Yes | It comes from the receiving mail server. No bot is involved. |
| **Booking (Cal.com)** | ✅ Yes | A person has to pick a time slot and submit a form. |
| **Unsubscribe** | ✅ Yes | Opening the link only shows a "Confirm" page. Only **pressing the button** (a POST request) unsubscribes. Bots open links but don't press buttons. Gmail's and Outlook's own "Unsubscribe" button (RFC 8058 one-click) is a real person's action. |
| **WhatsApp delivered / read / replies** | ✅ Yes | They come from Meta's servers. Link previews from WhatsApp itself are filtered as bot. |
| **Verified page visit** (new) | ✅ Yes | The person stayed 15+ seconds on the page, scrolling or moving, or opened a second page. Scanners fetch a page and leave. Needs one line on the website ([guide](../guides/visit-tracking.md)). |
| **Link click** | ⚠️ Mostly | A 6-check bot filter removes most scanners. Some delayed "sandbox" scanners can still get through (section 5), so a click alone is a hint, not proof. |
| **Open** | ❌ No | Apple marks everyone as having opened, Outlook hides real readers, and Gmail shows only the first open. **Off by default** (`EMAIL_OPEN_PIXEL=false`). Even when on, it scores 0 and is only a note on the lead (`human_opens`). |

**Rule of thumb:** a lead becomes "hot" only on 15+ points (pricing and booking clicks are 10 each,
case study / product 5, homepage 2; a verified visit adds the same again, once per page). A reply or
a booking moves a lead forward directly, with no points needed.

**Safety switch for the first real leads (`CLICK_ALERTS_NEED_CHECK`, on by default):** while a lead
hasn't replied, booked or been seen reading a page, clicks alone:

- never send the "hot lead" alert to sales. They send a **`click_check`** alert instead: "link
  clicks only, may be a company security scanner, check before contacting";
- never trigger an automatic email to the lead (the "pick a time" email after a pricing or booking
  click waits until a person is confirmed).

The points still count. A verified visit, a reply or a booking works exactly as usual and does reach
sales. Turn the switch off once a few weeks of real data show the click filter holds.

---

## 2. What the bots of each mail provider actually do

| Provider | Opens (image loading) | Link clicks | What it means for us |
|---|---|---|---|
| **Gmail** (personal and Google Workspace) | Loaded through Google's image proxy **when the person opens**. Usually only the first open is seen. The IP is Google's, not the reader's. | Normally doesn't visit links. | Gmail clicks are mostly real. Gmail opens are roughly real but undercounted. |
| **Apple Mail** (iPhone, iPad, Mac, Mail Privacy Protection) | Loads every image **on delivery**, whether or not anyone reads the email. | Doesn't visit links. | We mark these opens as `machine`. For Apple users we learn nothing from opens. |
| **Outlook / Microsoft 365 with Defender Safe Links** (seen live in our demo) | Loads the image 30–60 s after delivery. | Visits **every** link, including unsubscribe and our hidden trap link. Sometimes runs JavaScript. Comes from Microsoft Azure IP ranges. | Caught by our filter (trap link, scanner IP list, "same network" rule). |
| **Outlook desktop / web** (person reading) | **Blocks images by default** until the reader clicks "Download pictures". | Real clicks go through Microsoft's Safe Links first, then reach us from the person's own browser. | Real readers often never show as "opened". |
| **Corporate gateways: Proofpoint, Mimecast, Barracuda, Symantec, Trend Micro** | Varies. | Rewrite links and often open them in a **sandbox browser** that runs JavaScript, sometimes **minutes after delivery**, from IP ranges we don't know yet. | ⚠️ **Our biggest risk.** Indian banks and NBFCs (our target customers) commonly use these. |

---

## 3. How a click is checked (the bot filter)

Every tracked link points to our own server (`/r/<signed token>`). We don't use the email tool's
link tracking. When someone visits a link:

1. We **record** the visit but don't act on it yet.
2. A small page in the visitor's browser runs a tiny script that confirms "a real browser was here",
   then forwards the visitor to the real page (the confirm takes under a second).
3. After **30 seconds** (the settle window, so a scanner's burst of visits has all arrived), the
   worker decides **human** or **bot**. A visit counts as human only if it passes every check:

| # | Check | Catches |
|---|---|---|
| 1 | Was it the **hidden trap link**? It's invisible, so no person can click it. | Scanners that visit every link (Defender) |
| 2 | Is the browser name or IP on the **known-scanner list** (`config/scanners.yaml`)? | Known vendors and known ranges |
| 3 | Did the **same network** (same /24) hit the trap link, or come from a listed scanner, within 1 hour? | Defender visits from Azure ranges not yet on the list |
| 4 | Did it arrive **less than 60 s after sending**? | Delivery-time scanners |
| 5 | Were **2 different links** clicked within 10 s? | Scanners that open everything at once |
| 6 | Did our **confirm script fail to run**? | Simple scanners that don't run JavaScript |

Only `human` clicks add points and move the lead. Bot clicks are kept for the record only. A visit
to the unsubscribe link is never acted on: only the button on the confirm page unsubscribes.

**Opens** (when switched on) wait 30 s in the same way and go through checks 2, 3 and 4. Opens
through Apple's proxy are marked `machine`.

### 3b. The upgrade: did a person actually read the page?

A scanner can click. It does not **read**. So when a tracked link forwards to one of our own pages
(home, pricing, case study, product), the address carries a signed token, and a small script on the
website (`/v.js`, [install guide](../guides/visit-tracking.md)) measures **active** reading: time
with the tab visible *and* the reader scrolling, moving or touching in the last 30 seconds.

| What the visitor did | Counted as |
|---|---|
| 15+ s of active reading and scrolled 40 % of the page (or 3+ interactions) | **verified visit** |
| Went on to a second page of the site, 8+ s active | **verified visit** |
| Opened the page and left within seconds | nothing (a glance or a scanner) |
| A scanner fetched the page, even running JavaScript, without scrolling or input | nothing |

A verified visit goes through the same bot filter (scanner IP ranges, the scanner's network), then
counts as a confirmed human signal: points, a **"verified visit: read the pricing page (70s of
active reading)"** hot-lead alert for sales, and for the pricing page the booking-slots email.

### 3c. Real leads don't read every email

Most people see one or two of the four emails, many see none. Two things raise the odds that the one
they see is ours (our own sender, `EMAIL_MODE=mailbox`; Instantly has its own schedule):

- **Send windows:** emails go out only on working days, 09:30–17:30 India time (`EMAIL_SEND_WINDOW`,
  `EMAIL_SEND_TZ`), never at 2 a.m. or on a Sunday, where they are buried by Monday morning.
- **Time-of-day rotation:** email 1 goes at the first slot, the follow-ups alternate afternoon /
  morning / afternoon (15:00, 10:30, 15:30, plus a per-lead offset of up to 40 minutes), so someone
  who only clears email after lunch still meets one of them.

Answers to a person who just replied are never held: they go out at once.

---

## 4. Proof that it works (what has been tested)

**Automated tests.** Each one sends a fake bot or human visit and checks the verdict:

| Test file | Tests | Covers |
|---|---|---|
| `tests/test_botfilter.py` | 12 | Every filter check above |
| `tests/test_click_confirm.py` | 22 | Confirm script, trap link, Defender-style bursts, opens from scanner networks, Apple opens |
| `tests/test_open_tracking.py` | 22 | Open pixel: signed token, repeat opens ignored for 5 min, waits for the settle window |
| `tests/test_redirect.py` | 13 | Signed links, tampered links rejected, unsubscribe confirm page |
| `tests/test_tracking_accuracy.py` | 12 | Scoring is not inflated by bots |
| `tests/test_visits.py` | 51 | Visit tokens, the beacon, the engagement rule, scanner visits marked bot, click then reading = verified hot lead |
| `tests/test_sendwindow.py` | 14 | Working-hours slots, weekend and night hold, time-of-day rotation |
| `tests/test_world.py` | 11 | The realistic simulation below reaches the right outcome for each kind of lead |

**Realistic simulation (`python -m outreach.world`).** Simulated leads go through the **real** agent
(intake, our own sender with its send window, tracked links, open image, visit beacon, webhooks,
worker, AI reply reading). Each lead behaves like a real person: most never read, some read one email
late, a few reply. Each inbox brings its own machines: Apple loads every image, Microsoft Defender
opens every link within a minute, bank sandboxes click links minutes later. Result for a realistic
mix of 100 leads (`--leads 100`, seed 7):

| | |
|---|---|
| Emails sent / really read by a person | 367 / 57 (25 of 100 people read anything) |
| Opens logged / counted as a person | 272 / 57 (215 were Apple, Defender or sandboxes) |
| Link clicks by people / by machines | 5 / 537 |
| Clicks the filter let through as "a person" | 17 (12 of them a smart bank sandbox: the known gap) |
| Verified page visits | 4, all real people |
| Hot-lead alerts to sales / wrong ones | 2 / **0** |
| "Check first" alerts / on machines only | 6 / 3 |
| Really interested leads caught | **4 of 4** (2 booked a demo, 2 verified hot) |
| Outcome as expected | 100 of 100 |

The story version (each kind of lead once, with timelines) is in
[docs/samples/realistic-run.md](../samples/realistic-run.md).

**Live demo on a real Microsoft 365 inbox (2026-11-17):**

- Defender opened the image and visited every link, including unsubscribe and the trap link, from
  Azure ranges `74.224.0.0/14` and `4.208.0.0/12` within about a minute. **All marked bot.** The
  lead did not move and was not unsubscribed.
- A real person then clicked a link and pressed unsubscribe. **Both were detected as human** and
  acted on.
- Outlook opens were being counted as human. This was **fixed** in commit `ffffb89`: opens now wait
  30 s, and a scanner's network marks its own opens as bot.

---

## 5. Where it can still break (honest list)

**A. A bot counted as a human. This is the dangerous case: it gives sales a false alert.**

1. **Delayed sandbox scanners** (Proofpoint, Mimecast and others at banks): if the gateway holds the
   email for a few minutes, check 4 (60 s) misses it. The sandbox runs JavaScript, so check 6 misses
   it too. If it also skips the hidden link and comes from an unknown IP, the click counts as human.
   A single *pricing* click is enough to make a lead look hot. **Limited by the safety switch:**
   while the lead hasn't replied, booked or been seen reading, sales get a `click_check` alert
   ("check first"), not a hot-lead alert, and the lead gets no automatic email. In the 100-lead
   simulation this produced 3 "check first" alerts on machines and **no** wrong hot-lead alert. The
   scanner's clicks still add points (they halve after 30 quiet days).
2. **The scanner list is small.** Today it holds 2 Microsoft ranges and 6 browser names. Scanners
   often pretend to be normal Chrome. Defender uses many more Azure ranges than the two we have seen.
3. **Instantly mode:** Instantly's open events probably don't carry the IP or browser, so only the
   60 s check applies to opens. Clicks are unaffected because they always go through our own link.

**B. A real person counted as a bot (we miss a real lead).**

4. A person browsing through a **shared company proxy** (such as Zscaler) on the same network as a
   scanner that hit the trap link: their click within that hour is marked bot.
5. A person who clicks **within 60 s of the send**, or clicks **two links within 10 s**. This is rare.
6. A person whose browser has **JavaScript turned off**. This is rare.

**C. Opens in general:** Apple counts everyone as having opened, Outlook hides real readers, and
Gmail shows only the first open. That's why opens stay off and score 0.

**D. Deliverability:** the hidden trap link may slightly raise suspicion on very strict spam filters.
It can be switched off (`email_honeypot`), which also removes check 1.

---

## 6. How to check it yourself before real leads

**Locally (2 minutes, no real emails).** Start `scripts/run_local`, run `dev seed`, then:

```bash
D=".venv/bin/python -m outreach.dev"
$D tick                                         # sends email 1 to the seeded leads
$D advance --hours 0.1                          # 6 min later: past the 60 s "too soon" check
$D sim click --lead 1 --tag pricing --scanner   # a scanner (script never runs) -> bot
$D sim email-open --lead 1 --ip 17.58.0.1       # Apple proxy -> machine (needs EMAIL_OPEN_PIXEL=1)
$D sim unsub-link --lead 1                      # only opens the confirm page -> not unsubscribed
$D sim click --lead 2 --tag pricing             # a person (script runs) -> human, +10 points
$D advance --hours 0.02 && $D tick              # let the 30 s settle window pass
$D show 1 --mask; $D show 2 --mask              # see each event's verdict and the score
```

The human click uses a **different lead** on purpose. All simulated visits come from the same IP
(127.0.0.1), so a human click on lead 1 an hour after its scanner visit would correctly be marked
bot by check 3 (same network as a scanner).

**The realistic simulation (1 minute, nothing leaves the machine):**

```bash
.venv/bin/python -m outreach.world                          # each kind of lead once, with timelines
.venv/bin/python -m outreach.world --leads 100 --out run.md # a realistic mix of 100 leads
```

It runs in its own temporary folder (own database, simulated clock, stand-in mail server) and never
touches `local.db` or `.env.local`.

**With real inboxes you own (do this before the first real batch):**

1. Send the sequence to your own test addresses on **Gmail, Outlook.com, Microsoft 365 (Defender),
   iCloud / Apple Mail** and, if at all possible, **one address behind Proofpoint or Mimecast** (a
   friendly contact at a bank).
2. **Don't touch them for 1 hour.** Then run `dev show <id> --mask`. Every event should be `bot` or
   `machine`, with **zero `human`**. Any "human" event here is a scanner getting through: check who
   owns its IP (rdap.arin.net) and add the range to `config/scanners.yaml`.
3. Click one link yourself from your phone on mobile data. It must show as `human`.
4. **For the first real batch:** leave `CLICK_ALERTS_NEED_CHECK` on. Clicks-only leads then arrive
   as `click_check` alerts. Before calling, look at the lead's events (`dev show <id> --mask`): a
   data-centre IP, or several links visited within a few minutes of delivery, points to a scanner.

---

## 7. In one sentence

> We **don't rely on opens at all**. We trust **replies, bookings, bounces, unsubscribe button
> presses and verified page visits** (a person read the page) completely. We trust **clicks only
> after a 6-check bot filter**, which reliably catches Microsoft Defender and Apple but can still be
> fooled by delayed bank sandbox scanners, so clicks alone only send a "check first" alert and never
> email the lead. A realistic 100-lead simulation caught every interested lead with zero wrong
> sales alerts.
