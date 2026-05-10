import pytest
import asyncio
from app.core.event_bus import EventBus, GameEvent


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe(GameEvent.PHASE_CHANGED, handler)
        await bus.publish(GameEvent.PHASE_CHANGED, phase="night", round_number=1)

        assert len(received) == 1
        assert received[0]["phase"] == "night"
        assert received[0]["round_number"] == 1

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self):
        bus = EventBus()
        results1 = []
        results2 = []

        async def handler1(**kwargs):
            results1.append(kwargs)

        async def handler2(**kwargs):
            results2.append(kwargs)

        bus.subscribe(GameEvent.SPEECH_MADE, handler1)
        bus.subscribe(GameEvent.SPEECH_MADE, handler2)
        await bus.publish(GameEvent.SPEECH_MADE, text="hello")

        assert len(results1) == 1
        assert len(results2) == 1

    @pytest.mark.asyncio
    async def test_no_subscriber_no_error(self):
        bus = EventBus()
        await bus.publish(GameEvent.GAME_OVER)  # Should not raise

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe(GameEvent.VOTE_CAST, handler)
        bus.unsubscribe(GameEvent.VOTE_CAST, handler)
        await bus.publish(GameEvent.VOTE_CAST, target=1)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_crash(self):
        bus = EventBus()
        results = []

        async def bad_handler(**kwargs):
            raise RuntimeError("oops")

        async def good_handler(**kwargs):
            results.append(True)

        bus.subscribe(GameEvent.PLAYER_DIED, bad_handler)
        bus.subscribe(GameEvent.PLAYER_DIED, good_handler)
        await bus.publish(GameEvent.PLAYER_DIED, seat=3)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_clear(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe(GameEvent.GAME_STARTED, handler)
        bus.clear()
        await bus.publish(GameEvent.GAME_STARTED)

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_string_event_type(self):
        bus = EventBus()
        received = []

        async def handler(**kwargs):
            received.append(kwargs)

        bus.subscribe("custom_event", handler)
        await bus.publish("custom_event", data=42)

        assert len(received) == 1
        assert received[0]["data"] == 42
