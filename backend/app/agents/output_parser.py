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
    def __init__(self, reason: str, *, code: str = "action_validation_error"):
        self.reason = reason
        super().__init__(reason, code=code)


class StrictCapabilityError(RuntimeError):
    """The configured provider explicitly rejects strict tool support."""


_TOOL_CALL_XML = re.compile(
    r"<tool_call>\s*<function=([\w.-]+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
_TOOL_CALL_PARAM = re.compile(r"<parameter=([\w.-]+)>(.*?)</parameter>", re.DOTALL)
_TOOL_CALL_JSON_BODY = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def extract_tool_call_xml(raw: object) -> Optional[tuple[str, dict[str, object]]]:
    """Parse a pseudo-XML tool call emitted as plain message content.

    Some reasoning models (e.g. MiMo) answer tool-bound requests with their
    chat template's ``<tool_call>`` markup instead of the API's structured
    ``tool_calls`` field. Two body forms are accepted: the parameter-tag
    variant (``<function=name><parameter=k>v</parameter>...``) and the
    Hermes-canonical JSON body (``{"name": ..., "arguments": {...}}``).
    Returns ``(function_name, arguments)`` or None when the content carries
    no such block. Values stay as extracted; schema coercion happens where
    the contract schema is known.
    """
    if not isinstance(raw, str) or "<tool_call>" not in raw:
        return None
    match = _TOOL_CALL_XML.search(raw)
    if match is not None:
        name, body = match.group(1), match.group(2)
        arguments: dict[str, object] = {
            key: value.strip()
            for key, value in _TOOL_CALL_PARAM.findall(body)
        }
        if arguments:
            return name, arguments
    json_match = _TOOL_CALL_JSON_BODY.search(raw)
    if json_match is not None:
        try:
            parsed = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            return None
        if (
            isinstance(parsed, dict)
            and isinstance(parsed.get("name"), str)
            and isinstance(parsed.get("arguments"), dict)
        ):
            return parsed["name"], parsed["arguments"]
    return None


def _schema_failure_code(error_types: set[str]) -> str:
    """Classify Pydantic schema failures without retaining response content."""
    if "missing" in error_types:
        return "action_payload_missing_field"
    if "extra_forbidden" in error_types:
        return "action_payload_extra_field"
    if any(
        item.endswith("_type") or item.endswith("_parsing")
        for item in error_types
    ):
        return "action_payload_wrong_type"
    return "action_payload_schema_invalid"


def extract_json_object(raw: object) -> object:
    """Extract one JSON object from a model response.

    Accepted forms, in order: a bare JSON object, a ```json```-fenced object,
    and finally the last balanced ``{...}`` span embedded in surrounding prose
    (models often prefix the payload with a sentence of reasoning). The
    returned value is still validated against the action schema by the
    caller, so prose text itself is never trusted as an action.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip().lstrip("\ufeff")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return _last_balanced_json_object(text)


def _last_balanced_json_object(text: str) -> object | None:
    """Return the last balanced ``{...}`` span in ``text`` that parses."""
    best: object | None = None
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    best = json.loads(text[start:index + 1])
                except json.JSONDecodeError:
                    pass
                start = -1
    return best


def coerce_payload_to_schema(payload: dict, schema: dict) -> dict:
    """Coerce string parameter values to the schema's primitive types.

    Text-extracted arguments (pseudo-XML tool calls) arrive as strings while
    contracts demand real integers, booleans and nulls. Idempotent for
    already-typed payloads; values that cannot be converted are left for
    schema validation to reject downstream.
    """
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not payload:
        return payload
    coerced = dict(payload)
    for key, value in payload.items():
        spec = properties.get(key)
        if not isinstance(spec, dict) or not isinstance(value, str):
            continue
        declared = spec.get("type")
        types = [declared] if isinstance(declared, str) else list(declared or [])
        normalized = value.strip().lower()
        if "null" in types and normalized in {"", "null", "none"}:
            coerced[key] = None
        elif "integer" in types or "number" in types:
            try:
                coerced[key] = int(value)
            except ValueError:
                try:
                    coerced[key] = float(value)
                except ValueError:
                    pass
        elif "boolean" in types:
            if normalized == "true":
                coerced[key] = True
            elif normalized == "false":
                coerced[key] = False
    return coerced


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
            payload = extract_json_object(payload)
            if payload is None:
                raise ToolCallError(
                    "action payload must be a JSON object",
                    code="action_payload_not_json",
                )

        if not isinstance(payload, dict):
            raise ToolCallError(
                "action payload must be a JSON object",
                code="action_payload_not_json",
            )

        payload = coerce_payload_to_schema(payload, contract.json_schema())
        try:
            command = ActionCommand.model_validate(payload, strict=True)
        except ValidationError as error:
            code = _schema_failure_code({item["type"] for item in error.errors()})
            raise ToolCallError("invalid action payload", code=code) from error

        schema = contract.json_schema()
        action_schema = schema["properties"]["action_type"]
        if command.action_type not in action_schema["enum"]:
            raise ToolCallError(
                "action type is not permitted by contract",
                code="action_type_not_permitted",
            )
        if (
            command.action_type in contract.actions_requiring_target
            and command.target_seat is None
        ):
            raise ToolCallError("action requires a target", code="target_required")
        if (
            command.action_type not in contract.actions_requiring_target
            and command.target_seat is not None
        ):
            raise ToolCallError("action must not include a target", code="target_forbidden")

        max_reasoning_length = schema["properties"]["reasoning"].get("maxLength")
        if (
            max_reasoning_length is not None
            and len(command.reasoning) > max_reasoning_length
        ):
            raise ToolCallError(
                "reasoning exceeds contract limit", code="reasoning_too_long"
            )
        return command

    def parse_tool_action(
        self, name: str, args: dict | str, contract: ActionContract
    ) -> ActionCommand:
        """Parse the sole action tool that was issued for a contract."""
        if name not in {contract.resolved_tool_name, contract.contract_id}:
            raise ToolCallError(
                "tool name does not match issued contract",
                code="strict_tool_name_mismatch",
            )
        return self.parse_action_payload(args, contract)

    def parse_strict_action_response(
        self, response: AIMessage, contract: ActionContract
    ) -> ActionCommand:
        """Accept exactly one native tool call for an issued strict contract."""
        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            raise ActionValidationError(
                "strict action response must contain one tool call",
                code="strict_tool_missing",
            )
        if len(tool_calls) != 1:
            raise ActionValidationError(
                "strict action response must contain one tool call",
                code="strict_tool_count_invalid",
            )
        tool_call = tool_calls[0]
        try:
            return self.parse_tool_action(
                tool_call["name"], tool_call.get("args", {}), contract
            )
        except ToolCallError as error:
            raise ActionValidationError(error.reason, code=error.code) from error

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

        # Pseudo-XML tool call markup (e.g. MiMo reasoning models)
        xml_call = extract_tool_call_xml(content)
        if xml_call is not None:
            fn_name, arguments = xml_call
            logger.info(f"Tool call parsed (xml fallback): {fn_name}")
            return ToolCallResult(
                function_name=fn_name, arguments=arguments, raw_text=content,
            )

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
        parsed = extract_json_object(raw)
        return parsed if isinstance(parsed, dict) else None
