from __future__ import annotations
import json
import re
import logging
from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel, ValidationError
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import AIMessage

from app.models.actions import NightAction, VoteAction
from app.models.contracts import ActionCommand, ActionContract
from app.core.action_validator import ActionValidationError

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of parsing an LLM tool call."""
    function_name: str
    arguments: dict
    raw_text: str = ""


class ToolCallError(ActionValidationError):
    """Raised when tool call validation fails, with reason for caller."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class StrictCapabilityError(RuntimeError):
    """The configured provider explicitly rejects strict tool support."""


class NightActionModel(BaseModel):
    action_type: str
    target_seat: int | None = None
    reasoning: str = ""


class VoteActionModel(BaseModel):
    target_seat: int | None = None
    reasoning: str = ""


class OutputParser:
    """Parses structured (JSON) and unstructured (text) LLM outputs with retries."""

    def __init__(self, max_retries: int = 1):
        self.night_parser = PydanticOutputParser(pydantic_object=NightActionModel)
        self.vote_parser = PydanticOutputParser(pydantic_object=VoteActionModel)
        self.max_retries = max_retries

    def parse_action_payload(
        self, payload: dict | str, contract: ActionContract
    ) -> ActionCommand:
        """Strictly parse one action payload issued for ``contract``."""
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as error:
                raise ToolCallError("action payload must be a JSON object") from error

        if not isinstance(payload, dict):
            raise ToolCallError("action payload must be a JSON object")

        try:
            command = ActionCommand.model_validate(payload, strict=True)
        except ValidationError as error:
            raise ToolCallError("invalid action payload") from error

        schema = contract.json_schema()
        action_schema = schema["properties"]["action_type"]
        if command.action_type not in action_schema["enum"]:
            raise ToolCallError("action type is not permitted by contract")
        if (
            command.action_type in contract.actions_requiring_target
            and command.target_seat is None
        ):
            raise ToolCallError("action requires a target")
        if (
            command.action_type not in contract.actions_requiring_target
            and command.target_seat is not None
        ):
            raise ToolCallError("action must not include a target")

        max_reasoning_length = schema["properties"]["reasoning"].get("maxLength")
        if (
            max_reasoning_length is not None
            and len(command.reasoning) > max_reasoning_length
        ):
            raise ToolCallError("reasoning exceeds contract limit")
        return command

    def parse_tool_action(
        self, name: str, args: dict | str, contract: ActionContract
    ) -> ActionCommand:
        """Parse the sole action tool that was issued for a contract."""
        if name != contract.contract_id:
            raise ToolCallError("tool name does not match issued contract")
        return self.parse_action_payload(args, contract)

    def parse_strict_action_response(
        self, response: AIMessage, contract: ActionContract
    ) -> ActionCommand:
        """Accept exactly one native tool call for an issued strict contract."""
        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls or len(tool_calls) != 1:
            raise ActionValidationError("strict action response must contain one tool call")
        tool_call = tool_calls[0]
        try:
            return self.parse_tool_action(
                tool_call["name"], tool_call.get("args", {}), contract
            )
        except ToolCallError as error:
            raise ActionValidationError(error.reason) from error

    def parse_night_action(self, raw: str, player_seat: int) -> NightAction:
        parsed = self._extract_json(raw)
        if parsed is None:
            return NightAction(player_seat=player_seat, action_type="pass")

        return NightAction(
            player_seat=player_seat,
            action_type=parsed.get("action_type", "pass"),
            target_seat=parsed.get("target_seat"),
            reasoning=parsed.get("reasoning", ""),
        )

    def parse_vote_action(self, raw: str, voter_seat: int) -> VoteAction:
        parsed = self._extract_json(raw)
        if parsed is None:
            return VoteAction(voter_seat=voter_seat, target_seat=None)

        target = parsed.get("target_seat")
        if target == 0:
            target = None

        return VoteAction(
            voter_seat=voter_seat,
            target_seat=target,
            reasoning=parsed.get("reasoning", ""),
        )

    def parse_speech(self, raw: str) -> str:
        # Try to extract from JSON if model wrapped it
        parsed = self._extract_json(raw)
        if parsed and "speech_text" in parsed:
            return parsed["speech_text"].strip()
        if parsed and "text" in parsed:
            return parsed["text"].strip()
        return raw.strip()

    def parse_tool_call(self, response: AIMessage) -> ToolCallResult | None:
        """Parse a tool call from an AIMessage response.

        Returns ToolCallResult if a tool was called, None otherwise.
        Falls back to parsing the text content as a pseudo function call
        for models that don't support native tool calling.
        """
        # Native tool calls (OpenAI/DeepSeek function calling)
        if hasattr(response, "tool_calls") and response.tool_calls:
            tc = response.tool_calls[0]
            name = tc.get("name", "")
            args = tc.get("args", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            logger.info(f"Tool call parsed (native): {name}({args})")
            return ToolCallResult(function_name=name, arguments=args)

        # Fallback: parse text for function call patterns
        content = response.content if hasattr(response, "content") else str(response)
        if not content:
            return None

        # Try: speak(text="...") or last_words(text="...")
        for fn_name in ("speak", "last_words"):
            # Named param: text="..." or text='...' or "text"="..." etc.
            pattern = rf'{fn_name}\s*\(\s*["\']?text["\']?\s*[:=]\s*["\'](.+?)["\']\s*\)'
            m = re.search(pattern, content, re.DOTALL)
            if m:
                logger.info(f"Tool call parsed (text fallback): {fn_name}")
                return ToolCallResult(
                    function_name=fn_name,
                    arguments={"text": m.group(1)},
                    raw_text=content,
                )

            # Also try with positional arg: speak("...")
            pattern2 = rf'{fn_name}\s*\(\s*["\'](.+?)["\']\s*\)'
            m2 = re.search(pattern2, content, re.DOTALL)
            if m2:
                logger.info(f"Tool call parsed (text fallback positional): {fn_name}")
                return ToolCallResult(
                    function_name=fn_name,
                    arguments={"text": m2.group(1)},
                    raw_text=content,
                )

        return None

    def _extract_json(self, raw: str) -> dict | None:
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
