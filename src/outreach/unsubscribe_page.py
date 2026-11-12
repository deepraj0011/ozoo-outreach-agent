"""Static HTML for the scanner-safe unsubscribe flow. No JS, no external assets, and nothing
user-controlled is interpolated (the form posts back to its own URL)."""
from __future__ import annotations

_STYLE = (
    "body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f6f4;color:#1d1d1b;"
    "margin:0;padding:48px 16px}main{max-width:420px;margin:0 auto;background:#fff;border-radius:12px;"
    "padding:32px;box-shadow:0 1px 3px rgba(0,0,0,.08)}h1{font-size:1.3rem;margin:0 0 12px}"
    "p{line-height:1.5;margin:0 0 20px}button{font:inherit;background:#1d1d1b;color:#fff;border:0;"
    "border-radius:8px;padding:10px 20px;cursor:pointer}"
)


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{title}</title><style>{_STYLE}</style></head><body><main>{body}</main></body></html>"
    )


CONFIRM_HTML = _page(
    "Unsubscribe",
    "<h1>Unsubscribe from Ozoo.ai messages?</h1>"
    "<p>You will no longer receive emails or WhatsApp messages from us.</p>"
    "<form method=\"post\"><button type=\"submit\">Unsubscribe</button></form>",
)

DONE_HTML = _page(
    "Unsubscribed",
    "<h1>You're unsubscribed</h1><p>You won't receive further emails or WhatsApp messages from Ozoo.ai.</p>",
)

NOT_FOUND_HTML = _page("Link not valid", "<h1>Link not valid</h1><p>This unsubscribe link is not valid.</p>")

ERROR_HTML = _page(
    "Something went wrong",
    "<h1>Something went wrong</h1><p>Please try again in a moment.</p>",
)

HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}
