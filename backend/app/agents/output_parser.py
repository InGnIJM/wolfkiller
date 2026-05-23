from __future__ import annotations
import json
import re
import logging
from dataclasses import dataclass
from typing import Optional
from pydantic import BaseModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import AIMessage

from app.models.actions import NightAction, VoteAction

logger = logging.getLogger(__name__)


@dataclass
class ToolCallResult:
    """Result of parsing an LLM tool call."""
    function_name: str
    arguments: dict
    raw_text: str = ""
    thinking_text: str = ""  # LLM 在调用工具前的内心思考


class ToolCallError(Exception):
    """Raised when tool call validation fails, with reason for caller."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


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

    def parse_night_action(self, raw: str, player_seat: int) -> NightAction:
        parsed = self._extract_json(raw)
        if parsed is None:
            return NightAction(player_seat=player_seat, action_type="pass")

        return NightAction(
            player_seat=player_seat,
            action_type=parsed.get("action_type", "pass"),
            target_seat=parsed.get("target_seat"),
            reasoning=parsed.get("reasoning", ""),
            thinking=parsed.get("thinking", ""),
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
            thinking=parsed.get("thinking", ""),
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
        # Extract the model's internal thinking from response.content
        # DeepSeek outputs reasoning text in content before calling a tool
        thinking = ""
        if hasattr(response, "content") and response.content:
            if isinstance(response.content, str):
                thinking = response.content.strip()
            elif isinstance(response.content, list):
                thinking = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in response.content
                ).strip()

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
            return ToolCallResult(function_name=name, arguments=args, thinking_text=thinking)

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
                    thinking_text=thinking,
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
                    thinking_text=thinking,
                )

        return None

    def _extract_json(self, raw: str) -> dict | None:
        raw = raw.strip()

        # Direct JSON parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # JSON in code block
        code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # JSON object in text
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None
