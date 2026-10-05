"""Best-effort capture of the exact messages sent to a model.

Training a small local model needs ``(prompt, completion)`` pairs. Completions
already live in the ``model_requests`` table, but prompts were never persisted:
``llm_calls.log`` records metadata only ("never prompt text"). This module fills
that single gap without touching game behaviour.

Design rules (all of them are load-bearing):

* **Never affects a game.** ``PromptSink.record`` is an in-memory append and
  ``PromptCapture.flush`` swallows every error after diagnosing once.
* **Never enters a checkpoint.** ``PromptCapture`` is a runtime handle held by
  ``GameService``; nothing here is serialisable state. The orchestration codec
  matches field sets exactly, so adding anything to it would stop old games
  from resuming.
* **Only the final text counts.** Callers record immediately before the provider
  invocation, so the sink holds what the model actually received — not the
  pre-rewrite prompt.
* **One record per logical request.** The last entry wins (it is the one that
  produced the accepted result) and ``attempt_count`` keeps the retries visible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROMPT_CAPTURE_FILENAME = "llm_prompts.jsonl"
PROMPT_CAPTURE_OPERATION = "llm_prompt"
PROMPT_CAPTURE_ENV = "PROMPT_CAPTURE_ENABLED"
_SCHEMA_VERSION = 1

# Capture defaults to on: the whole point is to accumulate training corpora, and
# a game that ran with capture off can never be recovered afterwards.
_CAPTURE_TRUTHY = ("1", "true", "yes", "on")
_CAPTURE_DEFAULT_ENABLED = True

# LangChain message classes expose ``type`` ("human"/"ai"), the night flow uses
# OpenAI-shaped dicts ("user"/"assistant"). Normalise both onto one vocabulary.
_ROLE_ALIASES = {
    "system": "system",
    "human": "user",
    "user": "user",
    "ai": "assistant",
    "assistant": "assistant",
}


def _plain(value: object) -> object:
    """Recursively convert frozen mappings/tuples into JSON-serializable data."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return str(value)


def _normalize_message(message: object) -> dict[str, str]:
    """Coerce one provider message into ``{"role": ..., "content": ...}``."""
    if isinstance(message, Mapping):
        role = message.get("role")
        content = message.get("content")
    else:
        role = getattr(message, "type", None)
        content = getattr(message, "content", None)
    if type(role) is not str:
        role = ""
    role = _ROLE_ALIASES.get(role.lower(), role.lower())
    # The project only ever sends plain strings; anything else is stringified so
    # a surprise shape degrades to lossy text instead of dropping the sample.
    return {"role": role, "content": content if type(content) is str else str(content)}


def _canonical_messages(messages: object) -> str:
    return json.dumps(
        messages, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def prompt_sha256(messages: object) -> str:
    """Digest of the stored messages, so a reader can re-verify fidelity."""
    return hashlib.sha256(_canonical_messages(messages).encode("utf-8")).hexdigest()


class PromptSink:
    """Per-call buffer: callers append the messages they are about to send.

    Duck-typed on purpose — ``roles/base.py`` calls ``record`` without importing
    this module, so roles never depend on capture details.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: list[dict[str, object]] = []

    def record(self, messages: object, *, tool_schema: object = None,
               tool_name: object = None) -> None:
        """Append one outgoing message list. Never raises, never touches disk."""
        try:
            entry: dict[str, object] = {
                "messages": [_normalize_message(item) for item in messages],  # type: ignore[union-attr]
                "tool_schema": None if tool_schema is None else _plain(tool_schema),
                "tool_name": tool_name if type(tool_name) is str else None,
            }
        except Exception:
            # A non-iterable message list must not be able to break a game.
            return
        self._entries.append(entry)

    @property
    def empty(self) -> bool:
        return not self._entries

    @property
    def attempt_count(self) -> int:
        return len(self._entries)

    @property
    def messages(self) -> list[dict[str, str]]:
        if not self._entries:
            return []
        return list(self._entries[-1]["messages"])  # type: ignore[arg-type]

    @property
    def tool_schema(self) -> object:
        return None if not self._entries else self._entries[-1]["tool_schema"]

    @property
    def tool_name(self) -> str | None:
        return None if not self._entries else self._entries[-1]["tool_name"]  # type: ignore[return-value]


def build_prompt_record(
    *, request_id: str, call_kind: str, round_number: int, phase: str, seat: int,
    contract_id: str | None, transport: str | None, attempt: int,
    model_id: str | None, request_digest: str | None, sink: PromptSink,
    now: datetime | None = None,
) -> dict[str, object] | None:
    """Build one JSONL record from a sink, or ``None`` when nothing was sent.

    Pure: never touches disk, so it is directly unit-testable. Returning ``None``
    for an empty sink keeps the four call sites free of extra guards.
    """
    if sink.empty:
        return None
    messages = sink.messages
    stamp = datetime.now(timezone.utc) if now is None else now
    return {
        "timestamp": stamp.isoformat(),
        "round": round_number,
        "phase": phase,
        "operation": PROMPT_CAPTURE_OPERATION,
        "seat": seat,
        "data": {
            "schema_version": _SCHEMA_VERSION,
            "call_kind": call_kind,
            "request_id": request_id,
            "contract_id": contract_id,
            "transport": transport,
            "attempt": attempt,
            "attempt_count": sink.attempt_count,
            "model_id": model_id,
            # Only the night path hashes the messages into request_digest; every
            # other path leaves it None rather than writing a misleading value.
            "request_digest": request_digest,
            "prompt_chars": sum(len(item["content"]) for item in messages),
            "prompt_sha256": prompt_sha256(messages),
            "tool_name": sink.tool_name,
            "tool_schema": sink.tool_schema,
            "messages": messages,
        },
    }


def parse_capture_flag(raw: object) -> bool:
    """Parse a capture flag, closing capture on anything unrecognised."""
    return type(raw) is str and raw.strip().lower() in _CAPTURE_TRUTHY


def capture_enabled(value: str | None = None) -> bool:
    """Environment-level switch. An unset variable keeps the default (on)."""
    raw = os.getenv(PROMPT_CAPTURE_ENV) if value is None else value
    if raw is None:
        return _CAPTURE_DEFAULT_ENABLED
    return parse_capture_flag(raw)


def resolve_capture_setting(stored: object, *, env_value: str | None = None) -> tuple[bool, str]:
    """Resolve the effective switch as ``(enabled, source)``.

    Precedence is UI > env > default. The UI value short-circuits, so an operator
    who flips the switch never has to wonder whether the environment is still
    winning — the reported source is rendered next to the toggle.
    """
    if type(stored) is bool:
        return stored, "ui"
    raw = os.getenv(PROMPT_CAPTURE_ENV) if env_value is None else env_value
    if raw is None:
        return _CAPTURE_DEFAULT_ENABLED, "default"
    return parse_capture_flag(raw), "env"


def prompt_capture_marker_exists(data_dir: str, game_id: str) -> bool:
    """Whether this game already latched prompt capture on.

    The capture file's own existence is the per-game latch: a game that started
    with capture on keeps capturing across a resume, and a game that started
    with it off never starts mid-way. Reading the live switch on resume instead
    would produce half-captured games.
    """
    return os.path.exists(os.path.join(data_dir, "games", game_id, PROMPT_CAPTURE_FILENAME))


def capture_enabled_for_game(requested: bool, marker_exists: bool) -> bool:
    """Effective per-game switch: ``requested_at_start or marker_exists``.

    ``requested`` is resolved once, when the game is created; recovery never
    resolves the operator switch, so flipping it cannot make an interrupted game
    start capturing in the middle. The marker then carries the decision forward
    on its own.
    """
    return bool(requested) or bool(marker_exists)


class PromptCapture:
    """Per-game JSONL writer: one persistent fd, one lock, best-effort."""

    def __init__(self, data_dir: str, game_id: str, *, record_enabled: bool = True) -> None:
        self.record_enabled = bool(record_enabled)
        self._game_id = game_id
        self._lock = threading.Lock()
        self._fd: int | None = None
        self._warned = False
        if self.record_enabled:
            self._open(data_dir, game_id)

    def _open(self, data_dir: str, game_id: str) -> None:
        """Open the append fd once. A failure degrades instead of raising."""
        try:
            directory = os.path.join(data_dir, "games", game_id)
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, PROMPT_CAPTURE_FILENAME)
            # A persistent fd matters: re-opening per line measured ~10.5 ms on a
            # WSL /mnt/e mount versus ~0.9 ms with the descriptor held open.
            self._fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        except Exception:
            self._fd = None
            self._diagnose()

    def flush(self, record: Mapping[str, object] | None) -> None:
        """Append one record. Never raises; a failure degrades permanently."""
        if record is None or not self.record_enabled:
            return
        fd = self._fd
        if fd is None:
            self._diagnose()
            return
        try:
            # Serialise outside the lock: only the write needs to be exclusive.
            payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        except Exception:
            self._degrade()
            return
        try:
            # CPython splits writes above the buffer size into several write()
            # calls, so concurrent seats would interleave into corrupt lines.
            with self._lock:
                if os.write(fd, payload) != len(payload):
                    raise OSError("short write")
        except Exception:
            # Degrade rather than retry: a partial line would corrupt every
            # later record appended after it.
            self._degrade()

    def _degrade(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        self._diagnose()

    def _diagnose(self) -> None:
        if self._warned:
            return
        self._warned = True
        logger.warning(
            "prompt capture degraded for game %s; the game continues normally",
            self._game_id,
        )

    def close(self) -> None:
        """Release the descriptor. Idempotent and never raises."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except Exception:
            pass
