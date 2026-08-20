"""Reproduce the guard/witch night LLM call exactly as the game does.

Renders the same prompts (PromptRenderer + game _SYSTEM_PROMPT) and invokes
the configured mimo model once per role, printing the raw response and the
parse result. No game files are modified.

Usage:  python repro_night_prompt.py
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MODEL_CONFIG_PATH", "data/models.json")

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from app.agents.game_rules import NIGHT_SYSTEM_PROMPT as _SYSTEM_PROMPT  # noqa: E402
from app.agents.llm_client import LLMClient, LLMClientConfig, derive_strict_base_url  # noqa: E402
from app.agents.prompt_renderer import PromptRenderer  # noqa: E402
from app.models.pipeline import (  # noqa: E402
    ActionContext, ActionCommand, SchedulePoint,
)
from app.roles.registry import builtin_registry  # noqa: E402
from app.stores.model_config_store import get_model_config_store  # noqa: E402
from app.stores.model_key_crypto import ModelKeyCrypto  # noqa: E402

ALIVE = tuple(range(1, 11))


def make_client() -> LLMClient:
    store = get_model_config_store()
    cfg = next(c for c in store.list_all() if c.model_id == "mimo-v2.5")
    key = ModelKeyCrypto().decrypt(cfg.api_key_encrypted)
    return LLMClient(config=LLMClientConfig(
        base_url=cfg.base_url,
        api_key=key,
        model_id=cfg.model_id,
        temperature=1.2,
        max_tokens=int(os.environ.get("REPRO_MAX_TOKENS", "1024")),
        strict_base_url=derive_strict_base_url(cfg.base_url, cfg.strict_base_url),
    ))


def build_context(role_id: str, contract, seat: int, extra_facts: dict | None = None) -> ActionContext:
    facts: dict = {
        "alive_seats": ALIVE,
        "phase": "night",
        "round_number": 1,
        # Mirrors the provider's server-side random injection.
        "RANDOM_HINT": random.randrange(len(ALIVE)),
    }
    facts.update(extra_facts or {})
    return ActionContext(
        game_id="repro",
        revision=1,
        facts=facts,
        contract_id=contract.contract_id,
        contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(),
        round_number=1,
        phase="night",
        window_id=f"repro:{role_id}",
        schedule_point=contract.schedule_point,
        actor_seat=seat,
        actor_role_id=role_id,
        actor_alive=True,
        resources={"antidote": 1, "poison": 1} if role_id == "wolf-killer-witch" else {},
        action_key=f"repro:{role_id}:1",
        counters={},
    )


def main() -> None:
    snapshot = builtin_registry.freeze()
    renderer = PromptRenderer()
    client = make_client()

    cases = [
        ("wolf-killer-guard", 4, None),
        ("wolf-killer-witch", 1, {"wolf_kill_target": 5}),
        ("wolf-killer-seer", 9, None),
    ]

    for role_id, seat, extra_facts in cases:
        spec = snapshot.require(role_id)
        contract = spec.contracts[0]
        context = build_context(role_id, contract, seat, extra_facts)
        prompt = renderer.render(spec, contract, context, history="")
        messages = [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)]

        print("=" * 70)
        print(f"ROLE={role_id} seat={seat} contract={contract.contract_id} "
              f"action_types={contract.action_types}")
        for line in prompt.splitlines():
            if line.startswith("UNTRUSTED_HISTORY"):
                print(line[:80] + "...")
            elif line and not line[0].isascii() and not line.startswith("eyJ"):
                print("  (base64 history omitted)")
            else:
                print("  " + line[:200])

        try:
            response = client.get_model().invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            print(f"--- RAW RESPONSE (type={type(content).__name__}, len={len(content) if isinstance(content, str) else '?'}) ---")
            print(repr(content)[:2000])
            if not isinstance(content, str):
                print(">> NOT TEXT -> provider would fall back")
                continue
            try:
                parsed = json.loads(content)
                print(">> json.loads: OK")
                try:
                    command = ActionCommand.model_validate(parsed, strict=True)
                    print(f">> model_validate: OK -> {command.action_type} target={command.target_seat} reasoning_len={len(command.reasoning)}")
                    print(f">> action_type in contract? {command.action_type in contract.action_types}")
                except Exception as exc:
                    print(f">> model_validate FAILED: {type(exc).__name__}: {exc}")
            except Exception as exc:
                print(f">> json.loads FAILED: {type(exc).__name__}: {exc}")
        except Exception as exc:
            print(f">> LLM INVOKE FAILED: {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
