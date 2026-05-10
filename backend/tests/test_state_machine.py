import pytest
from app.core.state_machine import GameStateMachine, GameEvent, GamePhase


class TestGameStateMachine:
    def test_initial_state(self):
        sm = GameStateMachine()
        assert sm.get_state() == GamePhase.WAITING

    def test_start_transition(self):
        sm = GameStateMachine()
        sm.transition(GameEvent.START)
        assert sm.get_state() == GamePhase.ROLE_DEAL

    def test_full_night_day_cycle(self):
        sm = GameStateMachine()

        sm.transition(GameEvent.START)
        sm.transition(GameEvent.ROLES_ASSIGNED)
        assert sm.get_state() == GamePhase.NIGHT

        sm.transition(GameEvent.NIGHT_ACTIONS_COMPLETE)
        assert sm.get_state() == GamePhase.DAWN

        sm.transition(GameEvent.DAWN_COMPLETE)
        assert sm.get_state() == GamePhase.LAST_WORDS

        sm.transition(GameEvent.LAST_WORDS_COMPLETE)
        assert sm.get_state() == GamePhase.SPEECH

        sm.transition(GameEvent.SPEECHES_COMPLETE)
        assert sm.get_state() == GamePhase.VOTE_CASTING

        sm.transition(GameEvent.VOTES_COMPLETE)
        assert sm.get_state() == GamePhase.VOTE_RESOLUTION

        sm.transition(GameEvent.VOTE_RESOLVED)
        assert sm.get_state() == GamePhase.NIGHT

    def test_invalid_transition_raises(self):
        sm = GameStateMachine()
        with pytest.raises(ValueError, match="Invalid transition"):
            sm.transition(GameEvent.NIGHT_ACTIONS_COMPLETE)

    def test_can_transition(self):
        sm = GameStateMachine()
        assert sm.can_transition(GameEvent.START) is True
        assert sm.can_transition(GameEvent.NIGHT_ACTIONS_COMPLETE) is False

    def test_game_over_from_vote(self):
        sm = GameStateMachine()
        sm.transition(GameEvent.START)
        sm.transition(GameEvent.ROLES_ASSIGNED)
        sm.transition(GameEvent.NIGHT_ACTIONS_COMPLETE)
        sm.transition(GameEvent.DAWN_COMPLETE)
        sm.transition(GameEvent.LAST_WORDS_COMPLETE)
        sm.transition(GameEvent.SPEECHES_COMPLETE)
        sm.transition(GameEvent.VOTES_COMPLETE)
        sm.transition(GameEvent.GAME_OVER)
        assert sm.is_terminal()

    def test_on_enter_exit_hooks(self):
        sm = GameStateMachine()
        entered = []
        exited = []

        sm.on_enter(GamePhase.NIGHT, lambda: entered.append("night"))
        sm.on_exit(GamePhase.WAITING, lambda: exited.append("waiting"))

        sm.transition(GameEvent.START)
        assert exited == ["waiting"]

        sm.transition(GameEvent.ROLES_ASSIGNED)
        assert entered == ["night"]

    def test_reset(self):
        sm = GameStateMachine()
        sm.transition(GameEvent.START)
        sm.transition(GameEvent.ROLES_ASSIGNED)
        sm.reset()
        assert sm.get_state() == GamePhase.WAITING

    def test_last_words_to_speech(self):
        """LAST_WORDS should go directly to SPEECH."""
        sm = GameStateMachine()
        sm.transition(GameEvent.START)
        sm.transition(GameEvent.ROLES_ASSIGNED)
        sm.transition(GameEvent.NIGHT_ACTIONS_COMPLETE)
        sm.transition(GameEvent.DAWN_COMPLETE)
        sm.transition(GameEvent.LAST_WORDS_COMPLETE)
        assert sm.get_state() == GamePhase.SPEECH
