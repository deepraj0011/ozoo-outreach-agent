"""Designed HTML for the follow-up emails (task 21).

A step version in templates/emails.yaml may carry a `rich:` block. Its plain-text body then gets an
HTML twin: the Ozoo header (click -> home page), the text, a clickable product image, a "book a
demo" button and a footer with the product links. Every link is a tracked /r/ link from the
template values; images are served by the agent at /img/<name> (static/email). Email 1 stays plain
(better inbox placement); the plain text always goes too.

In the body, a line `[[image]]` or `[[button]]` marks where that block goes in the HTML; the plain
text drops the markers. A line "Label: <url>" becomes a text link "Label" in the HTML, and an
"Unsubscribe" one moves to the footer.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static" / "email"
IMAGE_RE = re.compile(r"^[a-z0-9-]{1,60}\.(?:png|jpg)$")
MARKERS = ("[[image]]", "[[button]]")
LINK_TAGS = ("homepage", "product", "voice", "chat", "pricing", "case_study", "trial", "booking")

PRIMARY = "#7a00d9"   # ozoo.ai --primary
INK = "#0b1437"       # ozoo.ai --ink
MUTED = "#5b6680"     # ozoo.ai --ink-muted
LINE = "#e6edf7"
PAGE_BG = "#f6f9fe"
FONT = "Arial,Helvetica,sans-serif"

_LABEL_LINK = re.compile(r"^(?P<label>[^\n:]{1,80}):\s*(?P<url>https?://\S+)$")
_URL = re.compile(r"https?://[^\s<>\"']+")


def image_file(name: str) -> Optional[str]:
    """`name` (no extension) -> the file name in static/email, or None."""
    for ext in ("jpg", "png"):
        f = f"{name}.{ext}"
        if IMAGE_RE.match(f) and (STATIC_DIR / f).is_file():
            return f
    return None


def validate_rich(where: str, rich: Any) -> None:
    if not isinstance(rich, dict):
        raise ValueError(f"{where}.rich must be a mapping")
    img = rich.get("image")
    if img is not None and img != "angle" and image_file(str(img)) is None:
        raise ValueError(f"{where}.rich.image: no static/email/{img}.jpg or .png")
    if rich.get("image_link", "product") not in LINK_TAGS:
        raise ValueError(f"{where}.rich.image_link must be one of {', '.join(LINK_TAGS)}")
    for key in ("image_alt", "caption", "button"):
        if key in rich and (not isinstance(rich[key], str) or len(rich[key]) > 160):
            raise ValueError(f"{where}.rich.{key} must be a short text")


def strip_markers(body: str) -> str:
    """The plain-text body: marker lines removed, no doubled blank lines left behind."""
    lines = [ln for ln in body.split("\n") if ln.strip() not in MARKERS]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _a(url: str, text: str, style: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}" style="{style}">{text}</a>'


def _line(line: str, unsub: List[str]) -> Optional[str]:
    m = _LABEL_LINK.match(line.strip())
    if m:
        label, url = m.group("label").strip(), m.group("url").rstrip(".,;)")
        if "unsubscribe" in label.lower():
            unsub.append(url)
            pre = label[: label.lower().rfind("unsubscribe")].strip()
            return html.escape(pre) if pre else None
        return _a(url, f"{html.escape(label)} &rarr;", f"color:{PRIMARY};font-weight:bold;text-decoration:none")
    out, pos = [], 0
    for u in _URL.finditer(line):
        out.append(html.escape(line[pos:u.start()]))
        out.append(_a(u.group(0), "this link", f"color:{PRIMARY}"))
        pos = u.end()
    out.append(html.escape(line[pos:]))
    return "".join(out)


def _image(rich: Mapping[str, Any], vals: Mapping[str, str], base: str) -> str:
    f = image_file(str(rich.get("image") or ""))
    if not f:
        return ""
    link = vals.get(f"{rich.get('image_link', 'product')}_link", "")
    alt = html.escape(str(rich.get("image_alt") or "Ozoo.ai product screen"), quote=True)
    img = (f'<img src="{html.escape(base + f, quote=True)}" width="536" alt="{alt}" style="display:block;'
           f'width:100%;max-width:536px;height:auto;border:1px solid {LINE};border-radius:8px">')
    cap = rich.get("caption")
    caption = (f'<div style="font-size:12px;color:{MUTED};padding-top:6px">{html.escape(str(cap))}</div>'
               if cap else "")
    return f'<div style="padding:8px 0 18px">{_a(link, img, "text-decoration:none")}{caption}</div>'


def _button(rich: Mapping[str, Any], vals: Mapping[str, str]) -> str:
    label = html.escape(str(rich.get("button") or "Book a 30-minute demo"))
    a = _a(vals.get("booking_link", ""), label, f"display:inline-block;padding:13px 26px;font-family:{FONT};"
           "font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:8px")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:6px 0 20px"><tr>'
            f'<td bgcolor="{PRIMARY}" style="border-radius:8px">{a}</td></tr></table>')


def _footer(vals: Mapping[str, str], unsub: List[str]) -> str:
    style = f"color:{MUTED};text-decoration:underline"
    links = " &middot; ".join(_a(vals[f"{t}_link"], n, style) for t, n in (
        ("product", "Post-Call Analytics"), ("voice", "Voice AI agents"), ("chat", "Conversation AI agents"),
        ("pricing", "Pricing")) if vals.get(f"{t}_link"))
    out = (f'<div style="padding-bottom:8px">{links}</div>'
           '<div>Ozoo.ai is the conversation AI product line of Habilelabs, Jaipur, India.</div>')
    if unsub:
        out += f'<div style="padding-top:8px">{_a(unsub[0], "Unsubscribe", style)}</div>'
    return out


def build_html(body: str, rich: Mapping[str, Any], vals: Mapping[str, str], image_base: str,
               preheader: str = "") -> str:
    """The designed HTML version of `body` (already filled in: no placeholders left)."""
    base = image_base.rstrip("/") + "/img/"
    unsub: List[str] = []
    blocks: List[str] = []
    for para in re.split(r"\n\s*\n", body.strip()):
        key = para.strip()
        if key == "[[image]]":
            blocks.append(_image(rich, vals, base))
            continue
        if key == "[[button]]":
            blocks.append(_button(rich, vals))
            continue
        lines = [x for x in (_line(ln, unsub) for ln in para.split("\n")) if x]
        if lines:
            blocks.append(f'<p style="margin:0 0 16px">{"<br>".join(lines)}</p>')
    header = _a(vals.get("homepage_link", ""), f'<img src="{html.escape(base + "header.png", quote=True)}" '
                f'width="600" alt="Ozoo.ai" style="display:block;width:100%;height:auto;border:0">',
                "text-decoration:none")
    pre = (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(preheader)}</div>'
           if preheader else "")
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title></title></head>'
        f'<body style="margin:0;padding:0;background:{PAGE_BG}">{pre}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PAGE_BG}"><tr>'
        '<td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;'
        'width:100%;background:#ffffff;border-radius:12px;overflow:hidden">'
        f'<tr><td>{header}</td></tr>'
        f'<tr><td style="padding:28px 32px 8px;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK}">'
        f'{"".join(blocks)}</td></tr>'
        f'<tr><td style="padding:16px 32px 24px;border-top:1px solid {LINE};font-family:{FONT};font-size:12px;'
        f'line-height:1.6;color:{MUTED}">{_footer(vals, unsub)}</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def with_tracking(doc: str, extras: str) -> str:
    """The pixel / trap link the sender adds, just before </body>."""
    if not extras:
        return doc
    i = doc.rfind("</body>")
    return doc[:i] + extras + doc[i:] if i >= 0 else doc + extras


def rich_parts(body: str, rich: Optional[Mapping[str, Any]], vals: Dict[str, str], image_base: str,
               preheader: str = "") -> Dict[str, str]:
    """{"text": plain body, "html": designed body or ""}."""
    text = strip_markers(body)
    if not rich:
        return {"text": text, "html": ""}
    return {"text": text, "html": build_html(body, rich, vals, image_base, preheader)}
