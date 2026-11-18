# Real-visit tracking: install the reading snippet on the website

**Why:** a click on an email link can be a bank's security scanner. What a scanner does not do is
**read** the page. This snippet tells the agent "a person really read this page" (at least 15
seconds of active reading plus scrolling, or a second page). The agent treats that as confirmed
interest: points, a verified hot-lead alert to sales, and, for the pricing page, the booking-slots
email.

**Without the snippet** nothing breaks: clicks are tracked and filtered as before, and clicks alone
stay "check first" alerts (`CLICK_ALERTS_NEED_CHECK`).

## 1. What the website team adds (once)

On every page our emails link to (home, pricing, case study, product), before `</body>`:

```html
<script src="https://<tracking host>/v.js" async></script>
```

`<tracking host>` is the agent's public host, the same one as in `LINK_BASE_URL` (for example
`go.ozoo.ai`). Adding it site-wide is fine: the script does nothing on a visit that did not come
from one of our emails.

If the site sends a **Content-Security-Policy** header, allow the host in both:

```
script-src  ... https://<tracking host>;
connect-src ... https://<tracking host>;
```

## 2. What the snippet does (and doesn't do)

- It only acts when the page was opened from a tracked email link: the address then carries
  `?oz_v=<signed token>`. It removes that parameter from the address bar right away, so it isn't
  shared onwards or bookmarked.
- It counts **active** time only: the tab is visible and the reader scrolled, moved the mouse,
  touched the screen or typed in the last 30 seconds. A tab left open in the background doesn't count.
- Once the visit is engaged it sends **one** small beacon (`{"a": seconds, "s": scroll %, "p": pages,
  "i": interactions}`) to `POST /v/<token>`. No cookies, no page content, no form data, no URL
  history. It keeps the token in `sessionStorage` for that tab only, so a second page still counts.
- It never throws an error into the page, and it has no dependencies (about 2 KB).
- The server re-checks the engagement rule, the bot filter runs on it like on clicks (scanner IP
  ranges, the scanner's network), and duplicates the same day are ignored.

Turn it off without touching the website: `VISIT_TRACKING=false`. Clicks then forward without the
token, and beacons are ignored.

Two settings on the website keep the token private (it is valid for 2 days after the click):

- `Referrer-Policy: strict-origin-when-cross-origin` (or stricter), the browser default today.
- Exclude the `oz_v` parameter in the analytics / tag manager (Google Analytics: "exclude URL query
  parameters"), because analytics may record the address before the snippet removes it.

## 3. Privacy

This measures how a **known lead** reads our site. Before turning it on:

- Get the sign-off on the go-live checklist item "Approval: website tracking script" (India's DPDP
  Act; GDPR for any EU visitor).
- Mention it in the website's privacy notice: "If you arrive from one of our emails, we note whether
  you read the page (time and scroll depth) to follow up sensibly."

## 4. Check it works

Locally, with the agent running (`scripts/run_local`):

```bash
D=".venv/bin/python -m outreach.dev"
$D sim click --lead 2 --tag pricing      # forwards to the pricing page with ?oz_v=...
$D sim visit --lead 2 --tag pricing --seconds 40 --scroll 70   # what the snippet sends
$D advance --hours 0.02 && $D tick
$D show 2 --mask                          # a "visit" event, verdict human, +10 points
$D sim visit --lead 2 --tag pricing --seconds 4               # a glance: not stored
```

On the real website, after installing:

1. Send yourself an email from the agent, click its pricing link, and read the page for 20 seconds
   while scrolling.
2. In the browser's developer tools (Network tab), look for one `POST /v/...` with status 204.
3. Run `dev show <id> --mask`: there should be a `visit` event with verdict `human`.
