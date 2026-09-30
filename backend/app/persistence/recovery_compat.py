"""How a checkpoint is judged against the code that is running now.

The resume machinery itself is version-independent; what used to block it was
comparing the *live* registry with the identity a game was frozen under. That
comparison can only ever answer "something changed", so this module answers the
useful question instead: which of the game's own durable identities moved, and
is the move safe to continue through?

Levels:

- ``exact``        — the live registry is the one the game was created with.
- ``compatible``   — the registry moved, but nothing this game uses moved.
- ``drift``        — something this game uses moved (a used contract's digest, or
                     the resource declaration its seats were set up with).
                     Resumable with explicit confirmation; never silently.
- ``incompatible`` — the game cannot be continued: a role or contract it uses is
                     gone, or a schema version moved under it.

Known limitation (deliberate, and documented in ``docs/architecture.md``): the
checkpoint records each role's *schema version* but not its declaration digest,
so a change limited to a seated role's tags or instructions cannot be told apart
from an unrelated change and lands in ``compatible``. Resource declarations and
used contracts — the two things that actually move in this repo's history — are
recorded, and those are what drive the ``drift`` verdict.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.core.role_runtime import resource_declaration_marker
from app.models.game import GameState
from app.roles.registry import RegistrySnapshot

_DIGEST = re.compile(r"^[0-9a-f]{64}$")

LEVELS = ("exact", "compatible", "drift", "incompatible")

EXACT = "exact"
COMPATIBLE = "compatible"
DRIFT = "drift"
INCOMPATIBLE = "incompatible"

# Refusal and confirmation codes. ``incompatible`` codes land in
# ``games.recovery_block_code``; ``drift`` codes are returned to the caller as
# the reason a confirmation is required (the game stays ``interrupted``).
REGISTRY_INCOMPATIBLE = "registry_incompatible"
CONTRACT_INCOMPATIBLE = "contract_incompatible"
ROLE_DECLARATION_DRIFT = "role_declaration_drift"
CONTRACT_DIGEST_DRIFT = "contract_digest_drift"
REGISTRY_IDENTITY_UNKNOWN = "registry_identity_unknown"


@dataclass(frozen=True)
class RecoveryAssessment:
    """A verdict plus the evidence behind it."""

    level: str
    code: str | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError("invalid recovery level")
        if self.code is not None and (type(self.code) is not str or not self.code):
            raise ValueError("invalid recovery code")
        if type(self.reasons) is not tuple or any(type(item) is not str or not item for item in self.reasons):
            raise ValueError("invalid recovery reasons")
        if self.level in (DRIFT, INCOMPATIBLE) and self.code is None:
            raise ValueError("recovery verdict requires a code")

    @property
    def resumable(self) -> bool:
        return self.level != INCOMPATIBLE

    @property
    def needs_confirmation(self) -> bool:
        return self.level == DRIFT


def _stored_requests(document: Mapping[str, object]) -> tuple[tuple[str, str, object, object], ...]:
    """Every ``(role_id, contract_id, version, digest)`` the journal recorded.

    Tolerant by design: a malformed document is the codec's problem (it refuses
    to decode one), and this module only reads what is there. ``assess`` has
    already rejected a non-mapping document.
    """
    journal = document.get("point_journal")
    if not isinstance(journal, list):
        return ()
    seen: list[tuple[str, str, object, object]] = []
    for entry in journal:
        if not isinstance(entry, Mapping):
            continue
        checkpoint = entry.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            continue
        for field in ("issued", "actual"):
            rows = checkpoint.get(field)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                role_id, contract_id = row.get("role_id"), row.get("contract_id")
                if type(role_id) is not str or type(contract_id) is not str:
                    continue
                item = (role_id, contract_id, row.get("contract_version"), row.get("contract_digest"))
                if item not in seen:
                    seen.append(item)
    return tuple(seen)


def assess(
    document: Mapping[str, object],
    state: GameState,
    registry: RegistrySnapshot,
) -> RecoveryAssessment:
    """Judge ``state`` (decoded from ``document``) against the live ``registry``."""
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    if type(state) is not GameState:
        raise TypeError("state must be GameState")
    if type(registry) is not RegistrySnapshot:
        raise TypeError("registry must be RegistrySnapshot")

    frozen = state.registry_digest
    seated = sorted({player.role for player in state.players.values()})
    reasons: list[str] = []

    missing = [role for role in seated if role not in registry.specs]
    if missing:
        return RecoveryAssessment(
            INCOMPATIBLE, REGISTRY_INCOMPATIBLE,
            tuple(f"role_missing:{role}" for role in missing),
        )

    for role in seated:
        stored = state.spec_versions.get(role)
        if stored is not None and stored != registry.require(role).schema_version:
            return RecoveryAssessment(
                INCOMPATIBLE, REGISTRY_INCOMPATIBLE,
                (f"role_spec_version:{role}:{stored}:{registry.require(role).schema_version}",),
            )

    for role_id, contract_id, version, digest in _stored_requests(document):
        if role_id not in registry.specs:
            return RecoveryAssessment(
                INCOMPATIBLE, REGISTRY_INCOMPATIBLE, (f"role_missing:{role_id}",),
            )
        matches = [item for item in registry.require(role_id).contracts if item.contract_id == contract_id]
        if len(matches) != 1:
            return RecoveryAssessment(
                INCOMPATIBLE, REGISTRY_INCOMPATIBLE, (f"contract_missing:{role_id}:{contract_id}",),
            )
        live = matches[0]
        if version != live.schema_version:
            return RecoveryAssessment(
                INCOMPATIBLE, CONTRACT_INCOMPATIBLE,
                (f"contract_schema_version:{contract_id}:{version}:{live.schema_version}",),
            )
        if digest != live.stable_digest():
            return RecoveryAssessment(
                DRIFT, CONTRACT_DIGEST_DRIFT, (f"contract_digest:{contract_id}",),
            )

    runtime = getattr(state, "_pipeline_runtime", None)
    stored_marker = getattr(runtime, "resource_setup_digest", None)
    identity = state.registry_digest if _DIGEST.fullmatch(state.registry_digest or "") else None
    if identity is None:
        # Nothing to compare the game's durable identities against: resumable,
        # but only with an explicit confirmation.
        return RecoveryAssessment(DRIFT, REGISTRY_IDENTITY_UNKNOWN, ("registry_identity_unknown",))
    declared_marker = resource_declaration_marker(state, registry.specs, identity)
    if stored_marker is not None and stored_marker != declared_marker:
        return RecoveryAssessment(DRIFT, ROLE_DECLARATION_DRIFT, ("resource_declaration",))
    if identity != registry.digest:
        return RecoveryAssessment(COMPATIBLE, None, ("registry_changed",))
    return RecoveryAssessment(EXACT)
