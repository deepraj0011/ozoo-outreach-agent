"""The local fake clock (APP_ENV=local only): real UTC time plus an offset kept in a small JSON
file, so `python -m outreach.dev advance --hours 48` moves time forward for the running server,
the worker inside it and the dev CLI alike (they all read the same file on every call).

Production never uses this: default_clock() returns the real clock unless app_env is "local".
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Union

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLOCK_FILE = REPO_ROOT / ".local_clock.json"
MAX_OFFSET = timedelta(days=3650)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalClock:
    """Callable now_fn: real time + the stored offset."""

    def __init__(self, path: Union[str, Path], real_now: Callable[[], datetime] = utcnow) -> None:
        self.path = Path(path)
        self._real_now = real_now

    def offset(self) -> timedelta:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            seconds = float(raw.get("offset_s", 0)) if isinstance(raw, dict) else 0.0
        except FileNotFoundError:
            return timedelta(0)
        except (OSError, ValueError, TypeError):
            logger.warning("local clock file unreadable; using no offset")
            return timedelta(0)
        off = timedelta(seconds=seconds)
        return off if abs(off) <= MAX_OFFSET else timedelta(0)

    def __call__(self) -> datetime:
        return self._real_now() + self.offset()

    def _write(self, off: timedelta) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".clock-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"offset_s": off.total_seconds()}, fh)
        os.replace(tmp, self.path)  # atomic: a reader never sees half a file

    def advance(self, delta: timedelta) -> datetime:
        off = self.offset() + delta
        if abs(off) > MAX_OFFSET:
            raise ValueError("offset too large")
        self._write(off)
        return self()

    def reset(self) -> datetime:
        self._write(timedelta(0))
        return self()


def clock_file(settings: Any) -> Path:
    raw = getattr(settings, "fake_clock_file", None)
    return Path(raw) if raw else DEFAULT_CLOCK_FILE


def default_clock(settings: Any) -> Callable[[], datetime]:
    """The app's now_fn: the fake clock only when APP_ENV=local, else real UTC time."""
    if getattr(settings, "app_env", "production") == "local":
        return LocalClock(clock_file(settings))
    return utcnow
