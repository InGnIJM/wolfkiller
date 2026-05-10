import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from app.models.game import GameState

logger = logging.getLogger(__name__)


class MemoryService:
    """Writes per-role memory files to disk each night phase."""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)

    def get_memory_dir(self, game_id: str) -> Path:
        return self.data_dir / "games" / game_id / "memories"

    def save_memories(self, state: GameState) -> None:
        memory_dir = self.get_memory_dir(state.game_id)
        memory_dir.mkdir(parents=True, exist_ok=True)

        for seat, player in state.players.items():
            memory = self._build_memory(state, seat, player)
            filepath = memory_dir / f"seat_{seat}_{player.role}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(memory, f, ensure_ascii=False, indent=2)

        logger.info(f"Memories saved: game={state.game_id}, round={state.round_number}")

    def load_memory(self, game_id: str, seat: int) -> dict | None:
        memory_dir = self.get_memory_dir(game_id)
        if not memory_dir.exists():
            return None
        pattern = f"seat_{seat}_*.json"
        matches = list(memory_dir.glob(pattern))
        if not matches:
            return None
        with open(matches[0], "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_memory(self, state: GameState, seat: int, player) -> dict:
        private_knowledge = self._build_private_knowledge(state, seat, player)
        action_history = self._build_action_history(state, seat)
        witnessed_events = self._build_witnessed_events(state)

        return {
            "game_id": state.game_id,
            "seat_number": seat,
            "role": player.role,
            "camp": player.camp.value if hasattr(player.camp, "value") else player.camp,
            "is_alive": player.is_alive,
            "private_knowledge": private_knowledge,
            "action_history": action_history,
            "witnessed_events": witnessed_events,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    def _build_private_knowledge(self, state: GameState, seat: int, player) -> dict:
        knowledge: dict = {
            "teammates": [],
            "check_results": [],
            "has_antidote": player.has_antidote,
            "has_poison": player.has_poison,
            "has_gun": player.has_gun,
            "last_wolf_kill_target": None,
        }

        if "werewolf" in player.role:
            knowledge["teammates"] = [
                s for s, p in state.players.items()
                if "werewolf" in p.role and s != seat
            ]

        if "seer" in player.role:
            knowledge["check_results"] = list(player.check_results)

        if "witch" in player.role:
            knowledge["last_wolf_kill_target"] = state.last_wolf_kill_target

        return knowledge

    def _build_action_history(self, state: GameState, seat: int) -> list[dict]:
        history: list[dict] = []

        for a in state.night_actions:
            if a.player_seat == seat:
                history.append({
                    "round": state.round_number,
                    "phase": "night",
                    "action": a.to_dict(),
                })

        for s in state.speeches:
            if s.player_seat == seat:
                history.append({
                    "round": s.round_number,
                    "phase": "speech",
                    "type": "speech",
                    "text": s.text,
                })

        for v in state.votes:
            if v.voter_seat == seat:
                history.append({
                    "round": state.round_number,
                    "phase": "vote_casting",
                    "type": "vote",
                    "vote": v.to_dict(),
                })

        return history

    def _build_witnessed_events(self, state: GameState) -> list[dict]:
        events: list[dict] = []
        for d in state.death_history:
            events.append({
                "round": d.round_number,
                "event": "death",
                "details": d.to_dict(),
            })
        return events
