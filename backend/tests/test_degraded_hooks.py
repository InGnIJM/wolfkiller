"""Every reasoning hook must say "the system acted", never "the player decided".

The 17 role modules each build a thought line for the god view, and all 17 used
to be reachable with an engine-made command: the placeholder ("系统异常，本轮未
行动") came back out dressed as ``决定今晚不使用药水：…``. That is what made the
god view read a system event as a player's motive, and what
``_log_audience_pairs`` then wrote into the seat's own history.

The hooks are discovered by scanning ``app/roles/*.py`` instead of listing them
here: a new role that publishes a thought has to opt into the degraded branch,
and a hand-written list would let it slip through the same way it did before.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from app.models.pipeline import (
    DEGRADED_REASONING, DEGRADED_THOUGHT, ActionCommand, ActionContext, SchedulePoint,
)

ROLES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "roles"
HOOK_SUFFIX = "_reasoning_effect"

#: Keyword-only extras the hooks add for their own wording. The degraded branch
#: returns before any of them is read, so any value works there; the second test
#: uses values that let each role's own branch render.
HOOK_EXTRAS = {"role_label": "狼人", "in_pool": True, "kills": True, "verdict": "identity"}


def _hook_signatures() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(ROLES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.endswith(HOOK_SUFFIX):
                found[f"{path.stem}.{node.name}"] = [
                    arg.arg for arg in (*node.args.args, *node.args.kwonlyargs)
                ]
    return found


SIGNATURES = _hook_signatures()
HOOK_IDS = sorted(SIGNATURES)


def _kwargs(dotted: str) -> dict[str, object]:
    """Only the extras, because ``context``/``command``/``ordinal`` go positionally."""
    return {name: HOOK_EXTRAS[name] for name in SIGNATURES[dotted][3:] if name in HOOK_EXTRAS}


def _load(dotted: str):
    module_name, function_name = dotted.split(".")
    module = __import__(f"app.roles.{module_name}", fromlist=[function_name])
    return getattr(module, function_name)


def _context() -> ActionContext:
    return ActionContext(
        game_id="degraded-game", revision=0, config_version="registry",
        contract_id="check", contract_version=1, contract_digest="0" * 64,
        round_number=2, phase="night", window_id="w", schedule_point=SchedulePoint.NIGHT_ACTION,
        actor_seat=1, actor_role_id="wolf-killer-seer", actor_alive=True, action_key="a",
        facts={"alive_seats": (1, 2, 3)},
    )


def test_every_role_hook_is_discovered() -> None:
    """A guard on the guard: the scan must actually find the whole family."""
    assert len(HOOK_IDS) == 18, HOOK_IDS


@pytest.mark.parametrize("dotted", HOOK_IDS)
def test_a_degraded_command_is_labelled_and_never_dressed_as_a_decision(dotted: str) -> None:
    hook = _load(dotted)
    command = ActionCommand(
        action_type="check", target_seat=2,
        reasoning=DEGRADED_REASONING, degraded=True,
    )

    effect = hook(_context(), command, 1, **_kwargs(dotted))
    payload = effect.payload["payload"]

    assert payload["degraded"] is True
    assert payload["reasoning"] == DEGRADED_REASONING
    assert payload["thought"] == DEGRADED_THOUGHT
    # The old wording leaked the engine's own failure text into the god view.
    assert "系统异常" not in payload["thought"]
    assert "决定" not in payload["thought"]


@pytest.mark.parametrize("dotted", HOOK_IDS)
def test_a_real_command_keeps_its_own_wording(dotted: str) -> None:
    hook = _load(dotted)
    command = ActionCommand(action_type="check", target_seat=2, reasoning="他像狼")

    effect = hook(_context(), command, 1, **_kwargs(dotted))
    payload = effect.payload["payload"]

    assert payload["degraded"] is False
    assert payload["reasoning"] == "他像狼"
    # The role's own branch ran: the placeholder must not appear at all.
    assert payload["thought"] != DEGRADED_THOUGHT
    assert "系统代行" not in payload["thought"]


def test_the_hooks_take_a_command_so_the_flag_can_reach_them() -> None:
    """The marker has to travel on the command; nothing else is available.

    ``resolve_effects`` is pure and never sees the request, and the effect
    applier only receives effects, so a hook that did not take the command could
    not know it was degrading at all.
    """
    for dotted, parameters in SIGNATURES.items():
        assert len(parameters) >= 3, dotted
        assert parameters[1] == "command", dotted
        assert "self" not in parameters, dotted


def test_the_discovered_hooks_are_plain_functions() -> None:
    """They are pure: no module state, no request, no engine."""
    for dotted in HOOK_IDS:
        assert inspect.isfunction(_load(dotted)), dotted
