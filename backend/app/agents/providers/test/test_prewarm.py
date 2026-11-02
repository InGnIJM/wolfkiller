"""The startup prewarm thread must never be able to break the server."""

from __future__ import annotations

import logging

from app.agents.providers import errors, prewarm, transports


def test_prewarm_imports_both_provider_stacks(monkeypatch, caplog) -> None:
    calls: list[str] = []
    monkeypatch.setattr(errors, "prewarm", lambda: calls.append("errors"))
    monkeypatch.setattr(transports, "prewarm", lambda: calls.append("transports"))

    with caplog.at_level(logging.INFO, logger=prewarm.__name__):
        prewarm.prewarm_providers()

    assert calls == ["errors", "transports"]
    assert any("prewarm finished" in record.getMessage() for record in caplog.records)


def test_a_failed_prewarm_is_logged_and_swallowed(monkeypatch, caplog) -> None:
    def _boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(errors, "prewarm", _boom)

    with caplog.at_level(logging.WARNING, logger=prewarm.__name__):
        prewarm.prewarm_providers()  # must not raise

    assert any("prewarm failed" in record.getMessage() for record in caplog.records)
