"""OpenAI-compatible server for the post-training student (WSL, 8GB GPU).

Serves the Qwen3-4B + LoRA adapter through ``/v1/chat/completions`` so the
WolfKiller backend can register it as a normal model config and run games.

The student was SFT'd to answer with JSON contracts / short Chinese speeches;
this server ignores the OpenAI tool schema and returns ``content`` text, which
the engine's JSON fallback path already consumes.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _TokenCap:
    """Hard cap on generated tokens; independent of the model's generation config."""

    def __init__(self, prompt_len: int, limit: int):
        self.prompt_len = prompt_len
        self.limit = limit

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        return int(input_ids.shape[-1]) - self.prompt_len >= self.limit


class _JsonBraceStop:
    """Stop once the first JSON object in the output is complete.

    Action contracts are one JSON object; without this the model sometimes keeps
    babbling after the closing brace, which the parsers have to salvage.
    """

    def __init__(self, tokenizer, prompt_len: int):
        self.tokenizer = tokenizer
        self.prompt_len = prompt_len
        self._text = ""
        self._done = False

    def __call__(self, input_ids, scores, **kwargs) -> bool:
        if self._done:
            return True
        new = input_ids[0, self.prompt_len :]
        self._text = self.tokenizer.decode(new, skip_special_tokens=True)
        stripped = self._text.lstrip()
        if not stripped.startswith("{"):
            return False
        depth, in_string, escaped = 0, False, False
        for char in stripped:
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
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    self._done = True
                    return True
        return False


class StudentService:
    def __init__(self, base: str, adapter: str, max_seq_len: int, max_new_tokens: int):
        self.max_seq_len = max_seq_len
        self.max_new_tokens = max_new_tokens
        self.lock = threading.Lock()
        self.model, self.tokenizer = self._load(base, adapter, max_seq_len)
        self.calls = 0
        self.tokens_out = 0

    @staticmethod
    def _load(base: str, adapter: str, max_seq_len: int):
        import torch
        from peft import PeftModel
        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=base,
            max_seq_length=max_seq_len,
            load_in_4bit=True,
            dtype=None,
        )
        model = PeftModel.from_pretrained(model, adapter)
        FastLanguageModel.for_inference(model)
        model.eval()
        return model, tokenizer

    def _prompt_ids(self, messages: list[dict]) -> list[int]:
        out = self.tokenizer.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=False
        )
        ids = out if isinstance(out, list) else out["input_ids"]
        return list(ids)

    def _truncate(self, ids: list[int]) -> list[int]:
        if len(ids) <= self.max_seq_len:
            return ids
        head = min(1024, self.max_seq_len // 4)
        return ids[:head] + ids[-(self.max_seq_len - head):]

    def complete(self, messages: list[dict], max_tokens: int) -> tuple[str, int, int]:
        import torch
        from transformers import StoppingCriteriaList

        prompt = self._truncate(self._prompt_ids(messages))
        limit = max(16, min(max_tokens, self.max_new_tokens))
        input_ids = torch.tensor([prompt], device=self.model.device)
        criteria = StoppingCriteriaList(
            [_TokenCap(len(prompt), limit), _JsonBraceStop(self.tokenizer, len(prompt))]
        )
        with self.lock, torch.no_grad():
            output = self.model.generate(
                input_ids=input_ids,
                max_new_tokens=limit,
                do_sample=False,
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
                stopping_criteria=criteria,
            )
        new_ids = output[0][len(prompt) :]
        text = self.tokenizer.decode(new_ids, skip_special_tokens=True).strip()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return text, len(prompt), int(new_ids.shape[-1])


def _chunk_text(text: str, size: int = 24):
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _speech_tool_name(payload: dict) -> str | None:
    """Return the speech tool the engine expects for this request, if any.

    The worker sends both ``speak`` and ``last_words``; the right one depends on
    the task instruction in the prompt (遗言 vs 白天/竞选发言).  Other contracts
    (votes, night actions, sheriff badge) are parsed from plain JSON content by
    the engine's existing JSON fallback and must not be wrapped.
    """
    tools = payload.get("tools") or []
    names = {
        (tool.get("function") or {}).get("name")
        for tool in tools
        if (tool.get("type") or "function") == "function"
    }
    if "speak" not in names and "last_words" not in names:
        return None
    last = ""
    for message in reversed(payload.get("messages") or []):
        if message.get("role") == "user":
            last = str(message.get("content") or "")
            break
    if "last_words" in names and "遗言" in last:
        return "last_words"
    return "speak" if "speak" in names else "last_words"


def _speech_text(text: str) -> str:
    """Unwrap ``{"text": ...}`` / ``{"speech_text": ...}`` if the model emitted JSON."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("text", "speech_text", "content"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return stripped


def make_handler(service: StudentService, model_name: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter default logging
            return

        def _json(self, code: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._json(200, {
                    "object": "list",
                    "data": [{"id": model_name, "object": "model", "owned_by": "wolfkiller"}],
                })
            elif self.path.rstrip("/") in ("/health", "/"):
                self._json(200, {"status": "ok", "model": model_name})
            else:
                self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._json(404, {"error": {"message": "not found"}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._json(400, {"error": {"message": "invalid json"}})
                return
            messages = payload.get("messages") or []
            max_tokens = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 512)
            stream = bool(payload.get("stream"))
            started = time.time()
            try:
                text, prompt_tokens, completion_tokens = service.complete(messages, max_tokens)
            except Exception as error:  # keep the game alive, surface the failure
                self._json(500, {"error": {"message": f"{type(error).__name__}: {error}"}})
                return
            elapsed = time.time() - started
            service.calls += 1
            service.tokens_out += completion_tokens
            request_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
            created = int(time.time())
            usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def emit(delta: dict, finish=None):
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_name,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                speech_tool = _speech_tool_name(payload)
                emit({"role": "assistant"})
                if speech_tool:
                    call_id = f"call_{uuid.uuid4().hex[:20]}"
                    arguments = json.dumps({"text": _speech_text(text)}, ensure_ascii=False)
                    emit({"tool_calls": [{
                        "index": 0, "id": call_id, "type": "function",
                        "function": {"name": speech_tool, "arguments": ""},
                    }]})
                    for piece in _chunk_text(arguments, 32):
                        emit({"tool_calls": [{
                            "index": 0, "function": {"arguments": piece},
                        }]})
                    emit({}, "tool_calls")
                else:
                    for piece in _chunk_text(text):
                        emit({"content": piece})
                    emit({}, "stop")
                usage_chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [],
                    "usage": usage,
                }
                self.wfile.write(f"data: {json.dumps(usage_chunk)}\n\n".encode("utf-8"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                print(
                    f"[{time.strftime('%H:%M:%S')}] {prompt_tokens}->{completion_tokens} tok "
                    f"{elapsed:.1f}s stream=True tool={_speech_tool_name(payload)} heads={text[:40]!r}",
                    flush=True,
                )
                return
            speech_tool = _speech_tool_name(payload)
            if speech_tool:
                arguments = json.dumps({"text": _speech_text(text)}, ensure_ascii=False)
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call_{uuid.uuid4().hex[:20]}",
                        "type": "function",
                        "function": {"name": speech_tool, "arguments": arguments},
                    }],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": text}
                finish = "stop"
            self._json(200, {
                "id": request_id,
                "object": "chat.completion",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            })
            print(
                f"[{time.strftime('%H:%M:%S')}] {prompt_tokens}->{completion_tokens} tok "
                f"{elapsed:.1f}s stream={stream} tool={_speech_tool_name(payload)} heads={text[:40]!r}",
                flush=True,
            )

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="/mnt/e/models/Qwen3-4B-Instruct-2507")
    parser.add_argument("--adapter", default="ckpt/wolfkiller-sft-strong/lora_final")
    parser.add_argument("--model-name", default="wolfkiller-sft")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--max-seq-len", type=int, default=12288)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args(argv)

    service = StudentService(args.base, args.adapter, args.max_seq_len, args.max_new_tokens)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service, args.model_name))
    print(
        f"serving {args.model_name} on http://{args.host}:{args.port}/v1 "
        f"(max_seq_len={args.max_seq_len})",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
