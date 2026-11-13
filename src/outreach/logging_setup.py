"""Logging for the web process and the worker CLI: the `outreach` logger at LOG_LEVEL (env,
default INFO) with one simple console handler. Idempotent. Records carry ids, kinds and exception
type names only (never contacts, message text or secrets: see the modules' own log lines)."""
from __future__ import annotations

import logging
import os

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MARK = "_outreach_handler"


def configure(level: str | None = None) -> logging.Logger:
    """Set up the `outreach` logger once. A host that already configured the root logger (pytest,
    a platform agent) keeps its handlers: records still propagate to them."""
    pkg = logging.getLogger("outreach")
    raw = (level or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    pkg.setLevel(getattr(logging, raw, logging.INFO) if isinstance(getattr(logging, raw, None), int)
                 else logging.INFO)
    if not any(getattr(h, _MARK, False) for h in pkg.handlers) and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMAT))
        setattr(handler, _MARK, True)
        pkg.addHandler(handler)
    return pkg
