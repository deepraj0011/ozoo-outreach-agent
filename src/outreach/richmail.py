"""Designed HTML for the follow-up emails (task 21, polished "Primary-first" in task 22).

A step version in templates/emails.yaml (or a custom template from the web app) may carry a `rich:`
block. Its plain-text body then gets an HTML twin that still reads like a personal email, because
heavy marketing layouts (banners, grey backgrounds, many links, hidden preheaders) are what push
mail into Gmail's Promotions tab:

- a white, left-aligned letter (no banner, no card), Ozoo ink and purple;
- one product screen, wrapped in a tracked link, with a short caption;
- a "> " paragraph becomes a purple-edged callout (a use case / scenario); "- " lines a list;
- one purple button (the tracked booking link);
- the signature with the Ozoo mark; a small, quiet unsubscribe line.

Body markers: a line `[[image]]` or `[[button]]` places that block (the plain text drops them).
"Label: <url>" lines become a text link "Label →". Every URL comes from the template values (tracked
/r/ links); every text is HTML-escaped. A version without `rich:` stays plain text.
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
MARK_IMAGE = "ozoo-mark.png"  # the Ozoo logo mark, next to the signature
TEXT_KEYS = ("image_alt", "caption", "button")
MAX_TEXT = 160

PRIMARY = "#7a00d9"   # ozoo.ai --primary
INK = "#0b1437"       # ozoo.ai --ink
INK_2 = "#33405c"     # ozoo.ai --ink-secondary
MUTED = "#5b6680"     # ozoo.ai --ink-muted
LINE = "#e6e8f0"
TINT = "#f6efff"      # a light wash of the primary, for the callout
FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

_LABEL_LINK = re.compile(r"^(?P<label>[^\n:]{1,80}):\s*(?P<url>https?://\S+)$")
_URL = re.compile(r"https?://[^\s<>\"']+")
_CALLOUT_LABEL = re.compile(r"^(?P<label>(?:Use case|In practice|Example|Result|Scenario|What changes)[^:]{0,30}):\s*", re.IGNORECASE)


def image_file(name: str) -> Optional[str]:
    """`name` (no extension) -> the file name in static/email, or None."""
    for ext in ("jpg", "png"):
        f = f"{name}.{ext}"
        if IMAGE_RE.match(f) and (STATIC_DIR / f).is_file():
            return f
    return None


def available_images() -> List[str]:
    """Image names (no extension) a template may use: the product screens and photos."""
    skip = {"header", MARK_IMAGE.rsplit(".", 1)[0]}
    return sorted(p.stem for p in STATIC_DIR.glob("*") if IMAGE_RE.match(p.name) and p.stem not in skip)


def validate_rich(where: str, rich: Any) -> None:
    if not isinstance(rich, dict):
        raise ValueError(f"{where}.rich must be a mapping")
    img = rich.get("image")
    if img not in (None, "", "angle") and image_file(str(img)) is None:
        raise ValueError(f"{where}.rich.image: no static/email/{img}.jpg or .png")
    if rich.get("image_link", "product") not in LINK_TAGS:
        raise ValueError(f"{where}.rich.image_link must be one of {', '.join(LINK_TAGS)}")
    for key in TEXT_KEYS:
        if key in rich and (not isinstance(rich[key], str) or len(rich[key]) > MAX_TEXT):
            raise ValueError(f"{where}.rich.{key} must be a text of at most {MAX_TEXT} characters")


def strip_markers(body: str, button_line: str = "") -> str:
    """The plain-text body: `[[button]]` becomes `button_line` ("Book a 30-minute demo: <link>"),
    `[[image]]` goes, callout "> " prefixes are dropped, no doubled blank lines left behind."""
    lines = []
    for ln in body.split("\n"):
        if ln.strip() == "[[button]]" and button_line:
            lines.append(button_line)
            continue
        if ln.strip() in MARKERS:
            continue
        lines.append(ln[2:] if ln.startswith("> ") else ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def _a(url: str, text: str, style: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}" style="{style}" target="_blank">{text}</a>'


def _inline(line: str) -> str:
    """A line of text: escaped; a bare URL becomes a short "this link"."""
    out, pos = [], 0
    for u in _URL.finditer(line):
        out.append(html.escape(line[pos:u.start()]))
        out.append(_a(u.group(0), "this link", f"color:{PRIMARY}"))
        pos = u.end()
    out.append(html.escape(line[pos:]))
    return "".join(out)


def _line(line: str, unsub: List[str]) -> Optional[str]:
    m = _LABEL_LINK.match(line.strip())
    if not m:
        return _inline(line)
    label, url = m.group("label").strip(), m.group("url").rstrip(".,;)")
    if "unsubscribe" in label.lower():
        unsub.append(url)
        return None
    return _a(url, f"{html.escape(label)}&nbsp;&rarr;", f"color:{PRIMARY};font-weight:600;text-decoration:none")


def _image(rich: Mapping[str, Any], vals: Mapping[str, str], base: str) -> str:
    f = image_file(str(rich.get("image") or ""))
    if not f:
        return ""
    link = vals.get(f"{rich.get('image_link', 'product')}_link", "")
    alt = html.escape(str(rich.get("image_alt") or "Ozoo.ai product screen"), quote=True)
    img = (f'<img src="{html.escape(base + f, quote=True)}" width="560" alt="{alt}" style="display:block;'
           f'width:100%;max-width:560px;height:auto;border:1px solid {LINE};border-radius:10px">')
    cap = rich.get("caption")
    caption = (f'<div style="font-size:12px;line-height:1.5;color:{MUTED};padding-top:8px">{html.escape(str(cap))}</div>'
               if cap else "")
    return f'<div style="margin:4px 0 22px">{_a(link, img, "text-decoration:none")}{caption}</div>'


def _button(rich: Mapping[str, Any], vals: Mapping[str, str]) -> str:
    label = html.escape(str(rich.get("button") or "Book a 30-minute demo"))
    a = _a(vals.get("booking_link", ""), label, f"display:inline-block;padding:12px 22px;font-family:{FONT};"
           "font-size:15px;font-weight:600;line-height:20px;color:#ffffff;text-decoration:none;border-radius:8px")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:2px 0 24px">'
            f'<tr><td bgcolor="{PRIMARY}" style="border-radius:8px">{a}</td></tr></table>')


def _callout(lines: List[str]) -> str:
    text = " ".join(ln[2:].strip() for ln in lines)
    m = _CALLOUT_LABEL.match(text)
    head = ""
    if m:
        head = (f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;'
                f'color:{PRIMARY};padding-bottom:4px">{html.escape(m.group("label"))}</div>')
        text = text[m.end():]
        again = _CALLOUT_LABEL.match(text)  # "Use case: {usecase}" where the use case has its own label
        text = text[again.end():] if again else text
        text = text[:1].upper() + text[1:]
    return (f'<div style="margin:0 0 20px;padding:14px 18px;background:{TINT};border-left:3px solid {PRIMARY};'
            f'border-radius:0 8px 8px 0;color:{INK_2};font-size:14px;line-height:1.6">{head}{_inline(text)}</div>')


def _bullets(lines: List[str], intro: Optional[str]) -> str:
    items = "".join(
        f'<tr><td valign="top" style="padding:0 10px 8px 0;color:{PRIMARY};font-weight:700">&bull;</td>'
        f'<td style="padding:0 0 8px;color:{INK}">{_inline(ln.strip()[2:])}</td></tr>' for ln in lines)
    lead = f'<p style="margin:0 0 10px">{_inline(intro)}</p>' if intro else ""
    return (f'{lead}<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin:0 0 18px;font-size:15px;line-height:1.55">{items}</table>')


def _signature(para: str, sig: str, base: str) -> str:
    """The closing ("Best,") and the signature lines, with the Ozoo mark beside the last ones."""
    before = para[: para.rfind(sig)].strip() if sig else ""
    lines = [ln for ln in sig.split("\n") if ln.strip()]
    name, rest = (lines[0], lines[1:]) if lines else ("", [])
    closing = f'<p style="margin:0 0 12px">{_inline(before)}</p>' if before else ""
    # not a link: fewer links read as a personal email (inbox placement)
    mark = (f'<img src="{html.escape(base + MARK_IMAGE, quote=True)}" width="32" height="32" alt="Ozoo.ai" '
            f'style="display:block;border:0;width:32px;height:32px">')
    extra = "".join(f'<div style="color:{MUTED};font-size:13px">{_inline(ln)}</div>' for ln in rest)
    return (f'{closing}<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 22px">'
            f'<tr><td valign="top" style="padding:2px 12px 0 0">{mark}</td>'
            f'<td style="font-size:14px;line-height:1.5"><div style="font-weight:600;color:{INK}">{_inline(name)}</div>'
            f'{extra}<div style="font-size:12px;color:{MUTED};padding-top:2px">Post-Call Analytics &middot; Voice AI agents'
            f' &middot; Conversation AI agents</div></td></tr></table>')


def _block(para: str, rich: Mapping[str, Any], vals: Mapping[str, str], base: str, unsub: List[str]) -> str:
    key = para.strip()
    if key == "[[image]]":
        return _image(rich, vals, base)
    if key == "[[button]]":
        return _button(rich, vals)
    lines = para.split("\n")
    sig = (vals.get("signature") or "").strip()
    if sig and key.endswith(sig):
        return _signature(key, sig, base)
    if all(ln.startswith("> ") for ln in lines):
        return _callout(lines)
    if all(ln.strip().startswith("- ") for ln in lines[1:]) and lines[-1].strip().startswith("- "):
        intro = None if lines[0].strip().startswith("- ") else lines[0]  # "Three things:" then "- …" lines
        return _bullets(lines if intro is None else lines[1:], intro)
    out = [x for x in (_line(ln, unsub) for ln in lines) if x]
    return f'<p style="margin:0 0 16px">{"<br>".join(out)}</p>' if out else ""


def build_html(body: str, rich: Mapping[str, Any], vals: Mapping[str, str], image_base: str) -> str:
    """The designed HTML version of `body` (already filled in: no placeholders left)."""
    base = image_base.rstrip("/") + "/img/"
    unsub: List[str] = []
    blocks = [_block(p, rich, vals, base, unsub) for p in re.split(r"\n\s*\n", body.strip())]
    foot = ""
    if unsub:
        foot = (f'<div style="border-top:1px solid {LINE};padding-top:12px;font-size:12px;line-height:1.5;color:{MUTED}">'
                f'Ozoo.ai, a Habilelabs product line, Jaipur, India. Not relevant? '
                f'{_a(unsub[0], "Unsubscribe", f"color:{MUTED};text-decoration:underline")}</div>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light"><title></title></head>'
        '<body style="margin:0;padding:0;background:#ffffff">'
        f'<div style="max-width:600px;padding:20px 20px 28px;font-family:{FONT};font-size:15px;line-height:1.6;'
        f'color:{INK};text-align:left">{"".join(blocks)}{foot}</div></body></html>'
    )


def with_tracking(doc: str, extras: str) -> str:
    """The pixel / trap link the sender adds, just before </body>."""
    if not extras:
        return doc
    i = doc.rfind("</body>")
    return doc[:i] + extras + doc[i:] if i >= 0 else doc + extras


def rich_parts(body: str, rich: Optional[Mapping[str, Any]], vals: Dict[str, str], image_base: str) -> Dict[str, str]:
    """{"text": plain body, "html": designed body or ""}."""
    label = str((rich or {}).get("button") or "Book a 30-minute demo")
    text = strip_markers(body, f"{label}: {vals.get('booking_link', '')}" if rich else "")
    if not rich:
        return {"text": text, "html": ""}
    return {"text": text, "html": build_html(body, rich, vals, image_base)}
