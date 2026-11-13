"""Task 10b: the office uses Microsoft Teams. No trace of the previous chat tool may remain."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = "sl" + "ack"  # spelled in two parts so this file does not match itself
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".html", ""}


def _files():
    yield ROOT / ".env.example"
    for base in (ROOT / "src", ROOT / "tests"):
        for p in base.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in TEXT_SUFFIXES:
                yield p


def test_no_old_chat_tool_left():
    hits = [str(p.relative_to(ROOT)) for p in _files() if OLD in p.read_text(encoding="utf-8").lower()]
    assert hits == []
