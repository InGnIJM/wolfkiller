"""OpenAI-compatible shim that fronts Devin ACP sessions.

Each OpenAI ``model`` id maps to one persistent ACP session, so a game seat
keeps its own conversation memory. WolfKiller (or any OpenAI client) can point
``base_url`` at this server and treat a Devin agent like a chat model.

    python scripts/devin_acp_shim.py --port 9100

Endpoints:
    POST /v1/chat/completions   OpenAI schema, stream + non-stream
    GET  /v1/models             live seat sessions
    GET  /v1/admin/sessions     model -> sessionId map
    POST /v1/admin/reset        drop all sessions (new game)
    GET  /healthz
"""

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEVIN_EXE = os.environ.get(
    "DEVIN_EXE",
    r"E:\Windsurf\resources\app\extensions\windsurf\devin\bin\devin.exe",
)

#: Suffix appended to the prompt when the request asks for structured output.
_SCHEMA_CLAUSE = (
    "\n\n【输出要求】你的回复必须是一个 JSON 对象，符合此 JSON Schema：\n{schema}\n"
    "只输出这个 JSON 对象本身，不要任何解释文字、markdown 代码块或其他内容。"
    "不要使用任何工具。"
)


def extract_json_object(text: str) -> dict | None:
    """Pull the first balanced ``{...}`` that parses to a dict.

    Braces inside double-quoted strings do not count: reasoning text can
    contain unbalanced brackets that would otherwise corrupt the scan.
    """
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    # A balanced {..} slice can only decode to a dict.
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None


def flatten_messages(messages: list[dict]) -> str:
    """Render an OpenAI message list as one plain prompt."""
    parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def build_prompt(request: dict) -> str:
    """Translate a chat.completions request body into the agent prompt."""
    prompt = flatten_messages(request.get("messages", []))
    schema = None
    response_format = request.get("response_format") or {}
    if response_format.get("type") in ("json_object", "json_schema"):
        schema = (response_format.get("json_schema") or {}).get("schema") or True
    tools = [t for t in request.get("tools") or []
             if t.get("type") == "function"]
    if len(tools) == 1:
        schema = tools[0].get("function", {}).get("parameters") or schema
    if schema is not None:
        schema_text = "{}" if schema is True else json.dumps(
            schema, ensure_ascii=False)
        prompt += _SCHEMA_CLAUSE.format(schema=schema_text)
    return prompt


def single_tool_name(request: dict) -> str | None:
    tools = [t for t in request.get("tools") or []
             if t.get("type") == "function"]
    if len(tools) == 1:
        return tools[0].get("function", {}).get("name")
    return None


def completion_body(request: dict, reply: str, usage: dict | None,
                    elapsed_ms: int) -> dict:
    """Assemble a non-streaming chat.completion response.

    ``content`` always carries the raw reply. When the request asked for
    exactly one function tool and the reply contained JSON, a fabricated
    tool_call is attached so tool-preferring clients take the primary path.
    """
    payload = extract_json_object(reply)
    tool_name = single_tool_name(request)
    message: dict[str, Any] = {"role": "assistant", "content": reply}
    finish = "stop"
    if tool_name and payload is not None:
        message["tool_calls"] = [{
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(payload, ensure_ascii=False),
            },
        }]
        finish = "tool_calls"
    choice: dict[str, Any] = {
        "index": 0, "message": message, "finish_reason": finish,
    }
    body: dict[str, Any] = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.get("model", "devin"),
        "choices": [choice],
        "shim": {"elapsed_ms": elapsed_ms, "transport": "devin-acp"},
    }
    if usage:
        body["usage"] = {
            "prompt_tokens": usage.get("input", 0),
            "completion_tokens": usage.get("output", 0),
            "total_tokens": usage.get("input", 0) + usage.get("output", 0),
        }
    return body


def sse_chunk(chunk_id: str, model: str, delta: dict,
              finish_reason: str | None = None) -> str:
    data = {
        "id": chunk_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": delta,
                     "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def openai_error(message: str, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "error": {"message": message, "type": "server_error", "code": code}})


class AcpConnection:
    """One ``devin acp`` subprocess; thread-safe request dispatcher."""

    def __init__(self, exe: str, workspace_root: str, devin_model: str | None):
        self.exe = exe
        self.workspace_root = workspace_root
        self.argv = [exe, "acp"] + (["--model", devin_model] if devin_model else [])
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._id_lock = threading.Lock()
        self._send_lock = threading.Lock()
        #: request id -> queue that receives exactly one response dict
        self._pending: dict[int, queue.Queue] = {}
        #: sessionId -> list of subscriber queues receiving notifications
        self._subscribers: dict[str, list[queue.Queue]] = {}
        self._sub_lock = threading.Lock()
        self.stderr_lines: list[str] = []
        self.authenticated = threading.Event()

    # ── lifecycle ────────────────────────────────────────────────

    def start(self):  # pragma: no cover - real subprocess
        os.makedirs(self.workspace_root, exist_ok=True)
        self.proc = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.workspace_root)
        threading.Thread(target=self._dispatch, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.call("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
        }, timeout=30)

    def close(self):  # pragma: no cover - real subprocess
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass

    def _drain_stderr(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            self.stderr_lines.append(line)
            for url in re.findall(r"https?://\S+", line):
                print(f"[acp stderr url] {url}")

    def _dispatch(self):
        """Route every stdout line to a waiter or session subscribers."""
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg_id = msg.get("id")
            if "method" in msg and msg_id is not None:
                self._answer_agent_request(msg)
            elif "method" in msg:
                self._broadcast(msg)
            elif msg_id is not None:
                waiter = self._pending.pop(msg_id, None)
                if waiter is not None:
                    waiter.put(msg)

    def _broadcast(self, msg: dict):
        session_id = msg.get("params", {}).get("sessionId")
        with self._sub_lock:
            queues = list(self._subscribers.get(session_id, ()))
        for q in queues:
            q.put(msg)

    def _answer_agent_request(self, msg: dict):
        method = msg["method"]
        if method == "session/request_permission":
            options = msg.get("params", {}).get("options", [])
            chosen = next((o for o in options if "allow" in str(
                o.get("kind", "")).lower()), None)
            if chosen:
                self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "outcome": {"outcome": "selected",
                                "optionId": chosen["optionId"]}}})
            else:
                self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "outcome": {"outcome": "cancelled"}}})
        else:
            self._write({"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601,
                                   "message": f"shim: {method} not implemented"}})

    # ── rpc ─────────────────────────────────────────────────────

    def _write(self, msg: dict):
        with self._send_lock:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self.proc.stdin.flush()

    def call(self, method: str, params: dict, timeout: float = 120) -> dict:
        with self._id_lock:
            self._id += 1
            req_id = self._id
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self._pending[req_id] = waiter
        self._write({"jsonrpc": "2.0", "id": req_id,
                     "method": method, "params": params})
        try:
            msg = waiter.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"{method} exceeded {timeout}s")
        if "error" in msg:
            raise RuntimeError(f"{method} -> {msg['error']}")
        return msg.get("result") or {}

    def subscribe(self, session_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._sub_lock:
            self._subscribers.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: queue.Queue):
        with self._sub_lock:
            subs = self._subscribers.get(session_id, [])
            if q in subs:
                subs.remove(q)

    # ── high level ──────────────────────────────────────────────

    def ensure_auth(self, api_key: str | None = None):
        try:
            self.session_new("__auth_probe__")
            self.authenticated.set()
            return
        except RuntimeError as err:
            if "authenticated" not in str(err):
                raise
        params: dict[str, Any] = {"methodId": "devin-browser"}
        if api_key:
            params["meta"] = {"api_key": api_key}
        print("[shim] authenticate: waiting for browser PKCE flow...")
        self.call("authenticate", params, timeout=300)
        self.authenticated.set()
        print("[shim] authenticated")

    def session_new(self, cwd: str) -> str:
        os.makedirs(cwd, exist_ok=True)
        result = self.call("session/new", {"cwd": cwd, "mcpServers": []},
                           timeout=60)
        return result["sessionId"]

    def prompt(self, session_id: str, text: str, timeout: float,
               on_chunk: Callable[[str], None] | None = None) -> dict:
        """Run one turn; returns {reply, stop_reason, usage, elapsed_ms}."""
        updates = self.subscribe(session_id)
        chunks: list[str] = []
        usage: dict[str, int] = {}
        started = time.monotonic()
        with self._id_lock:
            self._id += 1
            req_id = self._id
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self._pending[req_id] = waiter
        self._write({
            "jsonrpc": "2.0", "id": req_id, "method": "session/prompt",
            "params": {"sessionId": session_id,
                       "prompt": [{"type": "text", "text": text}]},
        })
        def consume(note):
            update = note.get("params", {}).get("update", {})
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                text_chunk = (update.get("content") or {}).get("text", "")
                if text_chunk:
                    chunks.append(text_chunk)
                    if on_chunk:
                        on_chunk(text_chunk)
            elif kind == "usage_update":
                meta = update.get("_meta") or {}
                usage["input"] = meta.get(
                    "cognition.ai/inputTokens", usage.get("input", 0))
                usage["output"] = meta.get(
                    "cognition.ai/outputTokens", usage.get("output", 0))

        try:
            while True:
                deadline = started + timeout
                try:
                    msg = waiter.get(timeout=0.05)
                    # The response lands after the last update is broadcast,
                    # but thread scheduling can race them: drain whatever is
                    # already queued so no trailing chunks are dropped.
                    while True:
                        try:
                            consume(updates.get_nowait())
                        except queue.Empty:
                            break
                    if "error" in msg:
                        raise RuntimeError(f"session/prompt -> {msg['error']}")
                    return {
                        "reply": "".join(chunks),
                        "stop_reason": (msg.get("result") or {}).get("stopReason"),
                        "usage": usage, "elapsed_ms": int(
                            (time.monotonic() - started) * 1000),
                    }
                except queue.Empty:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(f"session/prompt exceeded {timeout}s")
                try:
                    consume(updates.get(timeout=0.05))
                except queue.Empty:
                    continue
        finally:
            self.unsubscribe(session_id, updates)


class SessionPool:
    """model id -> persistent ACP session."""

    def __init__(self, conn: AcpConnection):
        self.conn = conn
        self._sessions: dict[str, dict] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def session_for(self, model: str) -> tuple[str, threading.Lock]:
        with self._guard:
            entry = self._sessions.get(model)
            if entry is None:
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", model) or "default"
                cwd = os.path.join(self.conn.workspace_root, "seats", safe)
                session_id = self.conn.session_new(cwd)
                entry = {"session_id": session_id, "lock": threading.Lock(),
                         "created": time.time()}
                self._sessions[model] = entry
            return entry["session_id"], entry["lock"]

    def reset(self) -> int:
        with self._guard:
            count = len(self._sessions)
            for model, entry in self._sessions.items():
                try:
                    self.conn.call("session/close", {
                        "sessionId": entry["session_id"]}, timeout=10)
                except Exception:
                    pass
            self._sessions.clear()
            return count

    def snapshot(self) -> dict:
        with self._guard:
            return {model: {"sessionId": e["session_id"],
                            "created": e["created"]}
                    for model, e in self._sessions.items()}


def _completions_impl(body: dict, pool: SessionPool,
                      prompt_timeout: float) -> Any:
    model = body.get("model") or "devin"
    prompt = build_prompt(body)
    if not prompt.strip():
        return openai_error("empty prompt", 400, "empty_prompt")
    try:
        session_id, lock = pool.session_for(model)
    except Exception as err:
        return openai_error(f"session_new failed: {err}", 500, "session_failed")
    stream = bool(body.get("stream"))
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if not lock.acquire(timeout=prompt_timeout):
        return openai_error("seat is busy with a previous turn",
                            409, "seat_busy")
    try:
        if stream:
            return _stream_response(pool, session_id, prompt, model, chunk_id,
                                    prompt_timeout)
        result = pool.conn.prompt(session_id, prompt, prompt_timeout)
        return JSONResponse(completion_body(
            body, result["reply"], result["usage"], result["elapsed_ms"]))
    except TimeoutError as err:
        return openai_error(str(err), 504, "prompt_timeout")
    except Exception as err:
        return openai_error(str(err), 500, "agent_error")
    finally:
        lock.release()


def _stream_response(pool: SessionPool, session_id: str, prompt: str,
                     model: str, chunk_id: str,
                     prompt_timeout: float) -> StreamingResponse:
    pieces: queue.Queue = queue.Queue()

    def on_chunk(text: str):
        pieces.put(text)

    def run_prompt():
        try:
            result = pool.conn.prompt(
                session_id, prompt, prompt_timeout, on_chunk=on_chunk)
            pieces.put(("__done__", result))
        except Exception as err:
            pieces.put(("__error__", err))

    thread = threading.Thread(target=run_prompt, daemon=True)
    thread.start()

    def events():
        yield sse_chunk(chunk_id, model, {"role": "assistant"})
        while True:
            item = pieces.get()
            if isinstance(item, tuple) and item[0] == "__done__":
                yield sse_chunk(chunk_id, model, {}, finish_reason="stop")
                yield "data: [DONE]\n\n"
                return
            if isinstance(item, tuple) and item[0] == "__error__":
                err = item[1]
                yield f"data: {json.dumps({'error': {'message': str(err)}})}\n\n"
                yield "data: [DONE]\n\n"
                return
            yield sse_chunk(chunk_id, model, {"content": item})

    return StreamingResponse(events(), media_type="text/event-stream")


def create_app(pool: SessionPool, prompt_timeout: float) -> FastAPI:
    app = FastAPI(title="devin-acp-shim")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "sessions": len(pool.snapshot())}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [
            {"id": name, "object": "model", "created": int(e["created"]),
             "owned_by": "devin-acp"}
            for name, e in pool.snapshot().items()]}

    @app.get("/v1/admin/sessions")
    def sessions():
        return pool.snapshot()

    @app.post("/v1/admin/reset")
    def reset():
        return {"dropped": pool.reset()}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        return _completions_impl(body, pool, prompt_timeout)

    return app


def main() -> int:  # pragma: no cover - CLI entry
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--exe", default=DEVIN_EXE)
    ap.add_argument("--workspace-root",
                    default=os.path.join(os.path.dirname(__file__),
                                         ".devin_shim_workspace"))
    ap.add_argument("--prompt-timeout", type=float, default=600.0)
    ap.add_argument("--api-key", default=os.environ.get("DEVIN_API_KEY"))
    ap.add_argument("--devin-model", default=os.environ.get("DEVIN_MODEL"))
    args = ap.parse_args()

    conn = AcpConnection(args.exe, os.path.abspath(args.workspace_root),
                         args.devin_model)
    conn.start()
    conn.ensure_auth(api_key=args.api_key)
    pool = SessionPool(conn)

    import uvicorn
    try:  # pragma: no cover - blocking server loop
        uvicorn.run(create_app(pool, args.prompt_timeout),
                    host=args.host, port=args.port, log_level="warning")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
