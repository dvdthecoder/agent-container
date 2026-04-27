"""Non-interactive opencode runner via ACP (Agent Client Protocol).

Usage: python3 opencode_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — custom OpenAI-compatible base URL (e.g. DeepSeek)
  OPENAI_API_KEY   — API key for the above endpoint
  OPENCODE_MODEL   — model name; if it contains '/' it's used as-is (e.g.
                     'github-models/deepseek/deepseek-v3-0324'); otherwise
                     it is prefixed with 'openai/' and mapped to the custom
                     base URL above.
  OPENCODE_WORKDIR — working directory inside the sandbox (default: /workspace)
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TASK = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
CWD = os.environ.get("OPENCODE_WORKDIR", "/workspace")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
RAW_MODEL = os.environ.get("OPENCODE_MODEL", "")

# Resolve the model to provider/model format.
if "/" in RAW_MODEL:
    # Already fully qualified (e.g. "github-models/deepseek/deepseek-v3-0324").
    MODEL_ID = RAW_MODEL
else:
    # Bare model name → assume the openai-compatible custom endpoint.
    MODEL_ID = f"openai/{RAW_MODEL}" if RAW_MODEL else "opencode/big-pickle"

TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# Write opencode config for custom OpenAI-compatible providers
# ---------------------------------------------------------------------------


def _write_config() -> None:
    """Write ~/.config/opencode/config.json for custom provider + model."""
    if not BASE_URL or not API_KEY or not RAW_MODEL or "/" in RAW_MODEL:
        # No custom base URL, or model is already fully qualified — skip.
        return

    cfg = {
        "model": MODEL_ID,
        "provider": {
            "openai": {
                "options": {"apiKey": API_KEY, "baseURL": BASE_URL},
                "models": {
                    RAW_MODEL: {
                        "name": RAW_MODEL,
                        "tool_call": True,
                        "temperature": True,
                    }
                },
            }
        },
    }
    cfg_dir = os.path.expanduser("~/.config/opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "config.json"), "w") as f:
        json.dump(cfg, f)


# ---------------------------------------------------------------------------
# ACP client
# ---------------------------------------------------------------------------


class AcpClient:
    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            ["opencode", "acp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._responses: queue.Queue[dict] = queue.Queue()
        self._output_lines: list[str] = []
        self._stop_reason: str = ""
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                self._responses.put(msg)
            else:
                self._handle_notification(msg)

    def _handle_notification(self, msg: dict) -> None:
        update = msg.get("params", {}).get("update", {})
        kind = update.get("sessionUpdate", "")
        if kind == "agent_message_chunk":
            chunk = update.get("content", "")
            if chunk:
                self._output_lines.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()
        elif kind == "session_completed" or kind == "session_error":
            self._stop_reason = kind

    def send(self, req: dict, timeout: float = 15.0) -> dict:
        assert self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(req) + "\n").encode())
        self._proc.stdin.flush()
        deadline = time.monotonic() + timeout
        req_id = req.get("id")
        pending: list[dict] = []
        while time.monotonic() < deadline:
            try:
                resp = self._responses.get(timeout=0.5)
            except queue.Empty:
                continue
            if resp.get("id") == req_id:
                # Re-queue any buffered responses for other calls.
                for p in pending:
                    self._responses.put(p)
                return resp
            pending.append(resp)
        for p in pending:
            self._responses.put(p)
        return {"error": "timeout", "id": req_id}

    def output(self) -> str:
        return "".join(self._output_lines)

    def terminate(self) -> None:
        try:
            self._proc.terminate()
        except Exception:  # noqa: S110
            pass  # best-effort — don't let teardown errors propagate


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not TASK:
        print("Usage: opencode_runner.py <task>", file=sys.stderr)
        return 1

    _write_config()

    client = AcpClient()
    _id = 0

    def req(method: str, params: dict, timeout: float = 15.0) -> dict:
        nonlocal _id
        _id += 1
        return client.send(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": _id},
            timeout=timeout,
        )

    # 1. Initialize
    init = req("initialize", {"protocolVersion": 1, "capabilities": {}})
    if "error" in init:
        print(f"ACP initialize failed: {init['error']}", file=sys.stderr)
        client.terminate()
        return 1

    # 2. Create session
    new_sess = req("session/new", {"cwd": CWD, "mcpServers": []})
    sid = new_sess.get("result", {}).get("sessionId", "")
    if not sid:
        print(f"session/new failed: {new_sess}", file=sys.stderr)
        client.terminate()
        return 1

    # 3. Resume (loads current model list; sets cwd context)
    req("session/resume", {"sessionId": sid, "cwd": CWD})

    # 4. Send prompt and wait for completion
    result = req(
        "session/prompt",
        {"sessionId": sid, "prompt": [{"type": "text", "text": TASK}]},
        timeout=float(TIMEOUT_SECONDS),
    )

    stop_reason = result.get("result", {}).get("stopReason", "")
    if stop_reason != "end_turn":
        error = result.get("error", "")
        print(f"\n[opencode] unexpected stop: {stop_reason or error}", file=sys.stderr)
        client.terminate()
        return 1

    client.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
