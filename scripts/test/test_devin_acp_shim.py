"""Unit tests for devin_acp_shim — pure logic + routes via a fake ACP conn.

Run:  cd scripts && python -m pytest test/test_devin_acp_shim.py \
          --cov=devin_acp_shim --cov-report=term-missing -q
"""

import json
import os
import sys
import threading

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import devin_acp_shim as shim  # noqa: E402


# ── pure helpers ────────────────────────────────────────────────

class TestExtractJsonObject:
    def test_clean_object(self):
        assert shim.extract_json_object('{"a": 1}') == {"a": 1}

    def test_embedded_in_text(self):
        text = 'sure! {"action_type": "vote", "target_seat": 3} hope this helps'
        assert shim.extract_json_object(text)["target_seat"] == 3

    def test_fenced_block(self):
        text = '```json\n{"x": "y"}\n```'
        assert shim.extract_json_object(text) == {"x": "y"}

    def test_nested(self):
        assert shim.extract_json_object('{"a": {"b": {"c": 1}}}') == {
            "a": {"b": {"c": 1}}}

    def test_skips_unbalanced_then_finds_next(self):
        assert shim.extract_json_object('{bad {"ok": true}') is None

    def test_no_json(self):
        assert shim.extract_json_object("plain text") is None

    def test_array_yields_inner_dict(self):
        # A bare array is not a dict, but the object inside it is fair game.
        assert shim.extract_json_object('[{"a": 1}]') == {"a": 1}

    def test_escaped_quotes_in_strings(self):
        payload = {"r": 'say "hi" {nested}'}
        assert shim.extract_json_object(json.dumps(payload)) == payload

    def test_balanced_but_invalid_skipped(self):
        assert shim.extract_json_object('{oops {"ok": 1}') is None
        assert shim.extract_json_object('{"a": } {"ok": 1}') == {"ok": 1}

    def test_parsed_non_dict_dropped(self):
        assert shim.extract_json_object('12 {"b": 2}') == {"b": 2}

    def test_braces_inside_strings_ignored(self):
        text = '{"action_type": "speech", "speech": "see } here"}'
        assert shim.extract_json_object(text)["speech"] == "see } here"


class TestFlattenMessages:
    def test_roles_and_order(self):
        out = shim.flatten_messages([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ])
        assert out == "[system]\nsys\n\n[user]\nu1\n\n[assistant]\na1"

    def test_content_block_list(self):
        out = shim.flatten_messages([{"role": "user", "content": [
            {"type": "text", "text": "hello "},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "text", "text": "world"},
        ]}])
        assert out == "[user]\nhello world"


class TestBuildPrompt:
    def test_plain_messages(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert shim.build_prompt(body) == "[user]\nhi"

    def test_single_tool_appends_schema(self):
        body = {
            "messages": [{"role": "user", "content": "vote"}],
            "tools": [{"type": "function", "function": {
                "name": "submit_action",
                "parameters": {"type": "object",
                               "properties": {"a": {"type": "integer"}}}}}],
        }
        out = shim.build_prompt(body)
        assert "submit_action" not in out  # name not leaked, schema is
        assert '"a"' in out and "输出要求" in out

    def test_two_tools_no_clause(self):
        body = {"messages": [{"role": "user", "content": "x"}],
                "tools": [{"type": "function", "function": {"name": "a"}},
                          {"type": "function", "function": {"name": "b"}}]}
        assert "输出要求" not in shim.build_prompt(body)

    def test_response_format_json_schema(self):
        body = {
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {"type": "json_schema", "json_schema": {
                "schema": {"type": "object"}}},
        }
        assert '"type": "object"' in shim.build_prompt(body)

    def test_response_format_bare_json_object(self):
        body = {"messages": [{"role": "user", "content": "x"}],
                "response_format": {"type": "json_object"}}
        assert "{}" in shim.build_prompt(body)


class TestSingleToolName:
    def test_one(self):
        body = {"tools": [{"type": "function",
                           "function": {"name": "act"}}]}
        assert shim.single_tool_name(body) == "act"

    def test_zero_and_two(self):
        assert shim.single_tool_name({}) is None
        assert shim.single_tool_name({"tools": [
            {"type": "function", "function": {"name": "a"}},
            {"type": "function", "function": {"name": "b"}}]}) is None


class TestCompletionBody:
    def test_plain(self):
        req = {"model": "devin-seat-3"}
        out = shim.completion_body(req, "hello", {"input": 10, "output": 5}, 42)
        assert out["choices"][0]["message"]["content"] == "hello"
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["usage"] == {"prompt_tokens": 10,
                                "completion_tokens": 5, "total_tokens": 15}
        assert out["shim"]["elapsed_ms"] == 42

    def test_tool_call_fabricated(self):
        req = {"model": "m", "tools": [{"type": "function", "function": {
            "name": "submit_action", "parameters": {}}}]}
        reply = 'thinking {"action_type": "vote", "target_seat": 7}'
        out = shim.completion_body(req, reply, None, 1)
        call = out["choices"][0]["message"]["tool_calls"][0]
        assert call["function"]["name"] == "submit_action"
        assert json.loads(call["function"]["arguments"])["target_seat"] == 7
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        assert "usage" not in out

    def test_tool_requested_but_no_json(self):
        req = {"tools": [{"type": "function",
                          "function": {"name": "act"}}]}
        out = shim.completion_body(req, "no json here", None, 1)
        assert "tool_calls" not in out["choices"][0]["message"]
        assert out["choices"][0]["finish_reason"] == "stop"


class TestSseChunk:
    def test_wire(self):
        line = shim.sse_chunk("c1", "m", {"content": "hi"}, None)
        assert line.startswith("data: ")
        payload = json.loads(line[6:])
        assert payload["choices"][0]["delta"] == {"content": "hi"}
        assert payload["choices"][0]["finish_reason"] is None


def drain(response) -> str:
    """Collect a StreamingResponse body synchronously."""
    import asyncio
    chunks = []

    async def collect():
        async for piece in response.body_iterator:
            chunks.append(
                piece.encode() if isinstance(piece, str) else piece)

    asyncio.run(collect())
    return b"".join(chunks).decode()


# ── fakes ───────────────────────────────────────────────────────

VOTE = '{"action_type": "vote", "target_seat": 5, "reasoning": "r"}'


class FakeConn:
    def __init__(self, reply=VOTE, err=None):
        self.workspace_root = ""
        self.reply = reply
        self.err = err
        self.created: list[str] = []
        self.prompts: list[tuple[str, str]] = []
        self.closed: list[str] = []

    def session_new(self, cwd):
        self.created.append(cwd)
        return f"sess-{len(self.created)}"

    def prompt(self, session_id, text, timeout, on_chunk=None):
        self.prompts.append((session_id, text))
        if self.err:
            raise self.err
        if on_chunk:
            for piece in ("he", "llo ", self.reply):
                on_chunk(piece)
            reply = "hello " + self.reply
        else:
            reply = self.reply
        return {"reply": reply, "stop_reason": "end_turn",
                "usage": {"input": 11, "output": 7}, "elapsed_ms": 9}

    def call(self, method, params, timeout=10):
        self.closed.append(params.get("sessionId", ""))
        return {}


def make_pool(conn=None):
    return shim.SessionPool(conn or FakeConn())


# ── SessionPool ─────────────────────────────────────────────────

class TestSessionPool:
    def test_session_per_model_and_reuse(self):
        conn = FakeConn()
        pool = shim.SessionPool(conn)
        s1, _ = pool.session_for("devin-seat-3")
        s2, _ = pool.session_for("devin-seat-3")
        s3, _ = pool.session_for("devin-seat-4")
        assert s1 == s2 != s3
        assert len(conn.created) == 2

    def test_reset_closes_and_clears(self):
        conn = FakeConn()
        pool = shim.SessionPool(conn)
        pool.session_for("a")
        pool.session_for("b")
        assert pool.reset() == 2
        assert pool.snapshot() == {}
        assert len(conn.closed) == 2

    def test_reset_tolerates_close_failure(self):
        conn = FakeConn()
        conn.call = lambda m, p, timeout=10: (_ for _ in ()).throw(
            RuntimeError("gone"))
        pool = shim.SessionPool(conn)
        pool.session_for("a")
        assert pool.reset() == 1
        assert pool.snapshot() == {}

    def test_snapshot_shape(self):
        pool = make_pool()
        pool.session_for("m")
        assert pool.snapshot()["m"]["sessionId"] == "sess-1"


# ── _completions_impl / routes ──────────────────────────────────


def _req(**over):
    body = {"model": "devin-seat-3",
            "messages": [{"role": "user", "content": "vote now"}]}
    body.update(over)
    return body


class TestCompletionsImpl:
    def test_non_stream_json(self):
        out = shim._completions_impl(_req(), make_pool(), 60)
        body = json.loads(out.body)
        assert body["choices"][0]["message"]["content"] == VOTE
        assert body["usage"]["total_tokens"] == 18

    def test_tool_call_attached(self):
        req = _req(tools=[{"type": "function", "function": {
            "name": "submit_action", "parameters": {}}}])
        out = shim._completions_impl(req, make_pool(), 60)
        body = json.loads(out.body)
        assert body["choices"][0]["finish_reason"] == "tool_calls"

    def test_prompt_includes_schema(self):
        conn = FakeConn()
        req = _req(tools=[{"type": "function", "function": {
            "name": "act", "parameters": {"x": 1}}}])
        shim._completions_impl(req, make_pool(conn), 60)
        assert '"x": 1' in conn.prompts[0][1]

    def test_empty_prompt_400(self):
        out = shim._completions_impl({"messages": []}, make_pool(), 60)
        assert out.status_code == 400
        assert json.loads(out.body)["error"]["code"] == "empty_prompt"

    def test_session_failure_500(self):
        conn = FakeConn()
        conn.session_new = lambda cwd: (_ for _ in ()).throw(ValueError("x"))
        out = shim._completions_impl(_req(), shim.SessionPool(conn), 60)
        assert out.status_code == 500

    def test_timeout_504(self):
        conn = FakeConn(err=TimeoutError("slow"))
        out = shim._completions_impl(_req(), make_pool(conn), 60)
        assert out.status_code == 504
        assert json.loads(out.body)["error"]["code"] == "prompt_timeout"

    def test_agent_error_500(self):
        conn = FakeConn(err=RuntimeError("boom"))
        out = shim._completions_impl(_req(), make_pool(conn), 60)
        assert out.status_code == 500
        assert json.loads(out.body)["error"]["code"] == "agent_error"

    def test_seat_busy_409(self):
        conn = FakeConn()
        pool = shim.SessionPool(conn)
        session_id, lock = pool.session_for("devin-seat-3")
        lock.acquire()
        try:
            out = shim._completions_impl(_req(), pool, 0.01)
        finally:
            lock.release()
        assert out.status_code == 409

    def test_stream_emits_chunks(self):
        out = shim._completions_impl(_req(stream=True), make_pool(), 60)
        data = drain(out)
        assert "chat.completion.chunk" in data
        assert "data: [DONE]" in data
        # role + 3 content pieces + finish + DONE
        assert data.count("data: ") >= 6

    def test_stream_agent_error(self):
        conn = FakeConn(err=RuntimeError("kaput"))
        out = shim._completions_impl(_req(stream=True), make_pool(conn), 60)
        data = drain(out)
        assert "kaput" in data and "[DONE]" in data


class TestRoutes:
    @pytest.fixture()
    def client(self):
        pool = make_pool()
        return TestClient(shim.create_app(pool, 60)), pool

    def test_healthz(self, client):
        http, _ = client
        assert http.get("/healthz").json() == {"ok": True, "sessions": 0}

    def test_models_grows_with_sessions(self, client):
        http, _ = client
        http.post("/v1/chat/completions", json=_req())
        models = http.get("/v1/models").json()["data"]
        assert [m["id"] for m in models] == ["devin-seat-3"]

    def test_admin_sessions_and_reset(self, client):
        http, pool = client
        http.post("/v1/chat/completions", json=_req())
        snap = http.get("/v1/admin/sessions").json()
        assert "devin-seat-3" in snap
        assert http.post("/v1/admin/reset").json() == {"dropped": 1}
        assert pool.snapshot() == {}

    def test_end_to_end_vote(self, client):
        http, _ = client
        resp = http.post("/v1/chat/completions", json=_req())
        assert resp.status_code == 200
        payload = json.loads(
            resp.json()["choices"][0]["message"]["content"])
        assert payload["action_type"] == "vote"


# ── AcpConnection internals (fake proc, no subprocess) ─────────


class TestInit:
    def test_argv_with_and_without_model(self):
        conn = shim.AcpConnection("exe", "ws", "opus")
        assert conn.argv == ["exe", "acp", "--model", "opus"]
        conn = shim.AcpConnection("exe", "ws", None)
        assert conn.argv == ["exe", "acp"]
        assert conn.proc is None and conn.stderr_lines == []

class FakeStdin:
    """BytesIO-like stdin that also records parsed messages."""

    def __init__(self):
        self.writes: list[dict] = []

    def write(self, data: bytes):
        self.writes.append(json.loads(data.decode()))

    def flush(self):
        pass


class FakeProc:
    def __init__(self, stdout_lines=(), stderr_lines=()):
        self.stdin = FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = iter(stderr_lines)


def bare_conn(stdout_lines=(), stderr_lines=()) -> shim.AcpConnection:
    """An AcpConnection wired to a fake proc (no subprocess, no threads)."""
    conn = shim.AcpConnection.__new__(shim.AcpConnection)
    conn.exe = "fake"
    conn.workspace_root = ""
    conn.argv = []
    conn.proc = FakeProc(stdout_lines, stderr_lines)
    conn._id = 0
    conn._id_lock = threading.Lock()
    conn._send_lock = threading.Lock()
    conn._pending = {}
    conn._subscribers = {}
    conn._sub_lock = threading.Lock()
    conn.stderr_lines = []
    conn.authenticated = threading.Event()
    return conn


class TestWrite:
    def test_writes_ndjson(self):
        conn = bare_conn()
        conn._write({"jsonrpc": "2.0", "id": 1, "method": "m", "params": {}})
        assert conn.proc.stdin.writes[0]["method"] == "m"


class TestCall:
    def _conn_with_auto_reply(self, reply):
        conn = bare_conn()

        def auto(msg):
            q = conn._pending[msg["id"]]
            q.put({"jsonrpc": "2.0", "id": msg["id"], **reply})

        conn._write = auto
        return conn

    def test_result(self):
        conn = self._conn_with_auto_reply({"result": {"ok": 1}})
        assert conn.call("m", {}) == {"ok": 1}

    def test_error_raises(self):
        conn = self._conn_with_auto_reply(
            {"error": {"code": -1, "message": "nope"}})
        with pytest.raises(RuntimeError, match="nope"):
            conn.call("m", {})

    def test_timeout(self):
        conn = bare_conn()
        conn._write = lambda msg: None  # never answers
        with pytest.raises(TimeoutError):
            conn.call("m", {}, timeout=0.05)
        assert conn._pending == {}


class TestDispatch:
    def test_routes_response_notification_and_request(self):
        import queue as q_mod
        lines = [
            b'{"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n',
            b'{"jsonrpc":"2.0","method":"session/update","params":'
            b'{"sessionId":"s1","update":{"sessionUpdate":"x"}}}\n',
            b'{"jsonrpc":"2.0","id":9,"method":"session/request_permission",'
            b'"params":{"options":[{"optionId":"o1","kind":"allow_once"}]}}\n',
            b'{"jsonrpc":"2.0","id":10,"method":"session/unknown_req"}\n',
            b'not json\n',
            b'\n',
        ]
        conn = bare_conn(stdout_lines=lines)
        waiter: q_mod.Queue = q_mod.Queue()
        conn._pending[7] = waiter
        sub = conn.subscribe("s1")
        conn._dispatch()
        assert waiter.get_nowait()["result"] == {"ok": True}
        assert sub.get_nowait()["params"]["sessionId"] == "s1"
        # permission auto-allowed; unknown request got a -32601 error
        writes = conn.proc.stdin.writes
        assert writes[0]["result"]["outcome"]["optionId"] == "o1"
        assert writes[1]["error"]["code"] == -32601

    def test_permission_no_allow_option_cancels(self):
        lines = [b'{"jsonrpc":"2.0","id":9,"method":"session/request_permission",'
                 b'"params":{"options":[{"optionId":"x","kind":"deny"}]}}\n']
        conn = bare_conn(stdout_lines=lines)
        conn._dispatch()
        assert conn.proc.stdin.writes[0]["result"]["outcome"]["outcome"] == \
            "cancelled"

    def test_drain_stderr_collects_and_prints_url(self, capsys):
        conn = bare_conn(stderr_lines=[
            b"log line one\n",
            b"open https://auth.example/abc now\n",
        ])
        conn._drain_stderr()
        assert conn.stderr_lines == [
            "log line one", "open https://auth.example/abc now"]
        assert "https://auth.example/abc" in capsys.readouterr().out

    def test_subscribe_unsubscribe(self):
        conn = bare_conn()
        q = conn.subscribe("s")
        conn._broadcast({"params": {"sessionId": "s"}})
        assert not q.empty()
        conn.unsubscribe("s", q)
        assert conn._subscribers["s"] == []


class TestEnsureAuth:
    def test_already_authed(self, tmp_path):
        conn = bare_conn()
        conn.session_new = lambda cwd: "sid"
        conn.ensure_auth()
        assert conn.authenticated.is_set()

    def test_browser_flow_then_ok(self):
        conn = bare_conn()
        calls = []
        conn.session_new = lambda cwd: (_ for _ in ()).throw(
            RuntimeError("host has not authenticated"))
        conn.call = lambda m, p, timeout=0: calls.append((m, p)) or {}
        conn.ensure_auth()
        assert calls[0][0] == "authenticate"
        assert calls[0][1]["methodId"] == "devin-browser"
        assert conn.authenticated.is_set()

    def test_api_key_goes_in_meta(self):
        conn = bare_conn()
        captured = {}
        conn.session_new = lambda cwd: (_ for _ in ()).throw(
            RuntimeError("host has not authenticated"))

        def fake_call(method, params, timeout=0):
            captured.update(params)
            return {}

        conn.call = fake_call
        conn.ensure_auth(api_key="k3y")
        assert captured["meta"] == {"api_key": "k3y"}

    def test_non_auth_error_propagates(self):
        conn = bare_conn()
        conn.session_new = lambda cwd: (_ for _ in ()).throw(
            RuntimeError("disk full"))
        with pytest.raises(RuntimeError, match="disk full"):
            conn.ensure_auth()


class TestSessionNewAndPrompt:
    def test_session_new_creates_dir(self, tmp_path):
        conn = bare_conn()
        conn.call = lambda m, p, timeout=0: {"sessionId": "s42"}
        target = str(tmp_path / "seat1")
        assert conn.session_new(target) == "s42"
        assert os.path.isdir(target)

    def test_prompt_collects_chunks_and_usage(self):
        conn = bare_conn()

        def fake_write(msg):
            # The agent answers asynchronously: two message chunks, one
            # usage update, then the prompt result.
            def respond():
                for sub in conn._subscribers["s1"]:
                    for update in (
                        {"sessionUpdate": "agent_message_chunk",
                         "content": {"text": "hel"}},
                        {"sessionUpdate": "agent_message_chunk",
                         "content": {"text": "lo"}},
                        {"sessionUpdate": "usage_update",
                         "_meta": {"cognition.ai/inputTokens": 3,
                                   "cognition.ai/outputTokens": 2}},
                        {"sessionUpdate": "agent_thought_chunk",
                         "content": {"text": "hmm"}},
                    ):
                        sub.put({"params": {"sessionId": "s1",
                                            "update": update}})
                conn._pending[msg["id"]].put(
                    {"result": {"stopReason": "end_turn"}})

            threading.Thread(target=respond, daemon=True).start()

        conn._write = fake_write
        seen = []
        out = conn.prompt("s1", "hi", 5, on_chunk=seen.append)
        assert out["reply"] == "hello"
        assert out["stop_reason"] == "end_turn"
        assert out["usage"] == {"input": 3, "output": 2}
        assert seen == ["hel", "lo"]

    def test_prompt_error_raises(self):
        conn = bare_conn()

        def fake_write(msg):
            conn._pending[msg["id"]].put(
                {"error": {"code": -1, "message": "bad"}})

        conn._write = fake_write
        with pytest.raises(RuntimeError, match="bad"):
            conn.prompt("s1", "hi", 5)

    def test_prompt_timeout(self):
        conn = bare_conn()
        conn._write = lambda msg: None
        with pytest.raises(TimeoutError):
            conn.prompt("s1", "hi", 0.2)
