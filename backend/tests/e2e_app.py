"""Test-only ASGI entry point used by Playwright browser acceptance tests.

Importing this module replaces the model-driving loop before the production
application is imported. No production route or environment flag can enable
the replacement.
"""

from __future__ import annotations

import asyncio

from app.core.game_engine import GameEngine


async def _idle_game_loop(self: GameEngine) -> None:
    while self._running:
        await self._wait_if_paused()
        await asyncio.sleep(0.05)


GameEngine._game_loop = _idle_game_loop

from app.main import app  # noqa: E402  (patch must precede application import)


__all__ = ["app"]
