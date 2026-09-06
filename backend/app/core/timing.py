"""Section 54: track useful performance durations without logging private data —
never the query text, incident content, or attachment content, only counts/timings/
identifiers that are already meaningless without DB access anyway.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger("app.perf")


@contextmanager
def log_duration(operation: str, **context: object) -> Iterator[None]:
    """Usage: `with log_duration("search", result_count=len(results)): ...` — logs
    after the block completes, at DEBUG (cheap to leave enabled; each call is one
    log line, not a per-row trace)."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        extra = " ".join(f"{k}={v}" for k, v in context.items())
        logger.debug("%s took %.1fms %s", operation, duration_ms, extra)
