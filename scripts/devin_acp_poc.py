"""PoC: drive a Devin session over ACP as a werewolf seat.

Spawns ``devin acp``, creates one session (the "seat"), sends two werewolf
prompts, and reports per-turn latency, stop reasons, JSON extraction, and
whether the second turn shows session memory.

Usage:
    python scripts/devin_acp_poc.py [--timeout 240] [--workspace DIR]
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEVIN_EXE = os.environ.get(
    "DEVIN_EXE",
    r"E:\Windsurf\resources\app\extensions\windsurf\devin\bin\devin.exe",
)

PROMPT_TURN1 = """你在玩一局12人狼人杀，你是3号位玩家，身份是【平民】（好人阵营，无夜间技能）。

当前局势：第1天白天。昨晚平安夜（无人死亡）。存活玩家：1-12号。
发言阶段刚结束，现在进入放逐投票。
候选人：2号、5号、8号（你也可以投给其他存活玩家或弃票）。
2号发言很划水，一直在跟风5号；5号跳了预言家但没人对跳；8号发言比较有逻辑。

规则：你必须只回复一个JSON对象，不要回复任何其他文字、解释或markdown代码块标记。
格式：{"action_type": "vote", "target_seat": <整数座位号或null表示弃票>, "reasoning": "<简短中文理由>"}
不要使用任何工具，不要读写文件，直接给出JSON回复。"""

PROMPT_TURN2 = """投票结果：5号被放逐出局，翻牌是狼人。

进入第2天白天：昨夜9号死亡（猎人，带走无人）。存活：1,2,3,4,6,7,8,10,11,12。
现在轮到放逐投票。2号昨天的表现你之前已经分析过了。11号今天跳女巫自称救了昨晚的7号（但死的是9号，对不上）。

规则：同样只回复一个JSON对象，不要其他文字。
格式：{"action_type": "vote", "target_seat": <整数座位号或null>, "reasoning": "<简短中文理由>"}
不要使用任何工具，直接给出JSON回复。"""


class AcpClient:
    """Minimal newline-delimited-JSON ACP client over a devin subprocess."""

    def __init__(self, exe: str, cwd: str):
        self.proc = subprocess.Popen(
            [exe, "acp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
        )
        self.inbox: queue.Queue[dict] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._id = 0
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for raw in self.proc.stdout:
            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except AttributeError:
                line = raw.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except json.JSONDecodeError:
                print(f"[non-json stdout] {line[:200]}")

    def _read_stderr(self):
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            self.stderr_lines.append(line)
            # Auth URLs and progress land on stderr; surface them live.
            if line:
                print(f"[stderr] {line[:300]}")
                for url in re.findall(r"https?://\S+", line):
                    print(f"\n>>> OPEN THIS URL TO LOG IN: {url}\n")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def send(self, method: str, params: dict, req_id: int | None = None):
        msg = {"jsonrpc": "2.0", "id": req_id if req_id is not None else self._next_id(),
               "method": method, "params": params}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return msg["id"]

    def reply(self, req_id, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result if result is not None else {}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def call(self, method: str, params: dict, timeout: float,
             on_update=None) -> tuple[dict, list[dict], float]:
        """Send a request; pump notifications/requests until the response arrives.

        Returns (result, updates, elapsed_seconds).
        """
        req_id = self.send(method, params)
        updates: list[dict] = []
        started = time.monotonic()
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError(f"{method} exceeded {timeout}s")
            try:
                msg = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"{method} exceeded {timeout}s")
            if msg.get("id") == req_id and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg["result"], updates, time.monotonic() - started
            if "method" in msg and "id" in msg:
                self._handle_agent_request(msg)
            elif "method" in msg:
                updates.append(msg)
                if on_update:
                    on_update(msg)

    def _handle_agent_request(self, msg: dict):
        method = msg["method"]
        if method == "session/request_permission":
            options = msg.get("params", {}).get("options", [])
            chosen = next(
                (o for o in options
                 if "allow" in str(o.get("kind", "")).lower()
                 or "allow" in str(o.get("name", "")).lower()),
                None,
            )
            if chosen:
                print(f"  [perm] auto-allow: {chosen.get('name') or chosen.get('optionId')}")
                self.reply(msg["id"], {"outcome": {
                    "outcome": "selected", "optionId": chosen["optionId"]}})
            else:
                print("  [perm] no allow option -> cancelled")
                self.reply(msg["id"], {"outcome": {"outcome": "cancelled"}})
        else:
            print(f"  [agent->client request] {method} -> not implemented")
            self.reply(msg["id"], error={"code": -32601, "message": "not implemented"})

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def extract_json(text: str):
    """Pull the first balanced {...} that parses as JSON."""
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = text[start:i + 1]
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    start = -1
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--workspace", default=os.path.join(
        os.path.dirname(__file__), ".devin_poc_workspace"))
    ap.add_argument("--exe", default=DEVIN_EXE)
    args = ap.parse_args()

    os.makedirs(args.workspace, exist_ok=True)
    transcript_path = os.path.join(args.workspace, "transcript.jsonl")
    transcript = open(transcript_path, "a", encoding="utf-8")

    client = AcpClient(args.exe, args.workspace)
    chunks: list[str] = []
    thoughts: list[str] = []

    def on_update(msg):
        update = msg.get("params", {}).get("update", {})
        kind = update.get("sessionUpdate")
        text = (update.get("content") or {}).get("text", "")
        if kind == "agent_message_chunk":
            chunks.append(text)
            print(text, end="", flush=True)
        elif kind == "agent_thought_chunk":
            thoughts.append(text)
        elif kind:
            print(f"\n  [update:{kind}] {str(update)[:160]}")

    try:
        print("=== initialize ===")
        t0 = time.monotonic()
        result, _, _ = client.call(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {}},
            timeout=30)
        print(f"ok in {time.monotonic()-t0:.1f}s; authMethods="
              f"{[m['id'] for m in result.get('authMethods', [])]}")

        print("=== session/new ===")
        try:
            result, _, elapsed = client.call(
                "session/new", {"cwd": args.workspace, "mcpServers": []},
                timeout=60)
        except RuntimeError as err:
            if "authenticated" not in str(err):
                raise
            print("auth required -> starting browser authenticate flow")
            print("watch stderr below for the URL if no browser opens")
            result, _, _ = client.call(
                "authenticate", {"methodId": "devin-browser"}, timeout=300)
            print(f"authenticate result: {result}")
            result, _, elapsed = client.call(
                "session/new", {"cwd": args.workspace, "mcpServers": []},
                timeout=60)
        session_id = result["sessionId"]
        print(f"sessionId={session_id} ({elapsed:.1f}s)")

        for turn, prompt in enumerate((PROMPT_TURN1, PROMPT_TURN2), start=1):
            chunks.clear()
            thoughts.clear()
            print(f"\n=== session/prompt turn {turn} ===")
            started = time.monotonic()
            try:
                result, updates, elapsed = client.call(
                    "session/prompt",
                    {"sessionId": session_id,
                     "prompt": [{"type": "text", "text": prompt}]},
                    timeout=args.timeout, on_update=on_update)
                stop = result.get("stopReason")
            except (TimeoutError, RuntimeError) as err:
                print(f"\n!! turn {turn} failed: {err}")
                break
            reply = "".join(chunks)
            parsed = extract_json(reply)
            print(f"\n--- turn {turn} report ---")
            print(f"elapsed: {elapsed:.1f}s | stopReason: {stop} "
                  f"| updates: {len(updates)}")
            print(f"reply chars: {len(reply)} | thought chars: {sum(len(t) for t in thoughts)}")
            print(f"extracted json: {json.dumps(parsed, ensure_ascii=False)}")
            valid = isinstance(parsed, dict) and "action_type" in parsed
            print(f"valid action json: {valid}")
            transcript.write(json.dumps({
                "turn": turn, "elapsed": elapsed, "stop_reason": stop,
                "reply": reply, "parsed": parsed,
            }, ensure_ascii=False) + "\n")
            transcript.flush()
    finally:
        transcript.close()
        client.close()
        if client.stderr_lines:
            tail = "\n".join(client.stderr_lines[-10:])
            print(f"\n[stderr tail]\n{tail}")
    print(f"\ntranscript: {transcript_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
