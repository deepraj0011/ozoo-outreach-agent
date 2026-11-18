"""python -m outreach.world [--leads N] [--seed S] [--days D] [--out report.md]

Runs in a fresh temporary directory (own database, own state, simulated clock, no network).
Never touches local.db, .env.local or any other local runtime file."""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from .engine import DAYS, simulate
from .report import collect, render


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m outreach.world", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leads", type=int, help="draw N leads from the realistic mix (default: each kind once)")
    ap.add_argument("--seed", type=int, default=7, help="random seed for --leads (same seed, same run)")
    ap.add_argument("--days", type=int, default=DAYS, help=f"simulated days (default {DAYS})")
    ap.add_argument("--out", help="also write the report (Markdown) to this file")
    args = ap.parse_args(argv)
    if args.leads is not None and not 1 <= args.leads <= 1000:
        ap.error("--leads must be between 1 and 1000")
    if not 1 <= args.days <= 365:
        ap.error("--days must be between 1 and 365")
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="ozoo-world-") as tmp:
        world = simulate(Path(tmp), args.leads, args.seed, args.days)
        text = render(collect(world), detailed=args.leads is None or args.leads <= 40)
        world.engine.dispose()
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
