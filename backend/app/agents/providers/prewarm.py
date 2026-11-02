"""Load the heavy provider SDKs off the request path.

Importing ``anthropic`` / ``openai`` (and the LangChain adapters) costs seconds
on a cold start, mostly because thousands of module files must be opened.
Nothing on the HTTP startup path needs them: transports and exception groups are
lazy. A daemon thread started once the app object is ready pays that bill in the
background, so the first game / LLM call after a restart does not have to.

The application starts a thread for this once the app object is ready; set
``WOLFKILLER_PROVIDER_PREWARM=0`` to disable it (the test suite does).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def prewarm_providers() -> None:
    """Import both provider stacks and fill the lazy exception caches."""
    started = time.monotonic()
    try:
        from app.agents.providers import errors, transports
        errors.prewarm()
        transports.prewarm()
    except Exception:
        logger.warning("Provider SDK prewarm failed", exc_info=True)
        return
    logger.info(
        "Provider SDK prewarm finished in %.1fs", time.monotonic() - started,
    )
