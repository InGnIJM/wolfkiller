"""The engine's vocabulary for death causes and win reasons.

These two sets used to exist only as ``str`` plus a comment on ``DeathReport`` /
``WinResult`` (``models/actions.py``), so nothing could enumerate them. Every
consumer kept its own hand-written copy — the public DTO, the old-archive replay
whitelist, the legacy-state reconstruction, the frontend types and its label
tables — and they drifted, silently, until an unlisted value reached a response
model and turned a request into HTTP 500.

Declaring the catalogues here makes "which values exist" a code fact. The
catalogues are the anchor for gates, **not** runtime values:

* ``persistence/checkpoint_codec.py`` and ``core/effect_applier.py`` validate
  strings with ``type(value) is str``, which an enum member fails (its ``type()``
  is the enum class). Passing members into death reports, effect payloads or
  events would raise instead of round-tripping, so producers keep writing plain
  strings and ``tests/test_values.py`` checks them against these catalogues.
* The public contract is asserted equal to the catalogue in
  ``tests/test_schemas.py`` (the DTO) and ``tests/test_game_routes.py`` (the
  replay whitelist), which is what makes a missing value a test failure instead
  of a production 500.
"""

from __future__ import annotations

from enum import Enum


class DeathCause(str, Enum):
    """Every cause a role or the engine can put on a death report.

    ``love_death`` stays reserved for a cause that is described in
    ``docs/gameplay.md`` but not implemented; it is deliberately absent so a
    producer cannot use it by accident.
    """

    WOLF_KILL = "wolf_kill"
    POISON = "poison"
    HUNTER_SHOT = "hunter_shot"
    EXILE = "exile"
    SELF_EXPLODE = "self_explode"
    KNIGHT_DUEL = "knight_duel"
    CHARM = "charm"
    WOLF_KING_SHOT = "wolf_king_shot"
    REFLECT = "reflect"
    DREAM_DEATH = "dream_death"


class WinReason(str, Enum):
    """Every reason ``core/rule_engine.py`` can end a game with.

    ``wolves_outnumber_good`` is the second most common ending, which is why
    leaving it out of the public DTO broke 22 real games before it was noticed.
    """

    ALL_GODS_DEAD = "all_gods_dead"
    ALL_VILLAGERS_DEAD = "all_villagers_dead"
    ALL_WOLVES_DEAD = "all_wolves_dead"
    WOLVES_OUTNUMBER_GOOD = "wolves_outnumber_good"


class WinningCamp(str, Enum):
    """The camps a finished game can be announced for.

    ``third_party`` is described in the domain comments but has no producer, so
    like ``love_death`` it stays out until something can emit it.
    """

    GOOD = "good"
    WEREWOLF = "werewolf"
