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
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TASK = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
CWD = os.environ.get("OPENCODE_WORKDIR", "/workspace")
# Strip trailing /v1 (or /v1/) — the OpenAI SDK appends /v1 itself, so
# if OPENAI_BASE_URL is "https://host/v1" all calls become .../v1/v1/... (404).
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
if BASE_URL.endswith("/v1"):
    BASE_URL = BASE_URL[:-3]
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
# Pre-flight: verify the model endpoint is reachable
# ---------------------------------------------------------------------------


def _preflight() -> bool:
    """Hit BASE_URL/v1/models and print what comes back. Returns True if OK."""
    if not BASE_URL:
        print(  # noqa: T201
            "[preflight] OPENAI_BASE_URL is not set — using built-in model",
            file=sys.stderr,
        )
        return True

    url = f"{BASE_URL}/v1/models"
    print(f"[preflight] checking model endpoint: {url}", file=sys.stderr)
    try:
        http_req = urllib.request.Request(  # noqa: S310 — URL comes from operator-controlled env var
            url, headers={"Authorization": f"Bearer {API_KEY}"}
        )
        with urllib.request.urlopen(http_req, timeout=30) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
            print(f"[preflight] endpoint OK ({resp.status}): {body[:200]}", file=sys.stderr)
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"[preflight] HTTP {e.code} from {url}: {body}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[preflight] FAILED to reach {url}: {e}", file=sys.stderr)
        return False


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
                        # Disable extended thinking — Qwen3/reasoning models can spin
                        # indefinitely on simple tasks when thinking is enabled.
                        "reasoning": False,
                    }
                },
            }
        },
    }
    cfg_dir = os.path.expanduser("~/.config/opencode")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "config.json")
    with open(cfg_path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"[config] wrote opencode config to {cfg_path}:", file=sys.stderr)
    print(f"  model={MODEL_ID}  base_url={BASE_URL}", file=sys.stderr)


# ---------------------------------------------------------------------------
# ACP client
# ---------------------------------------------------------------------------


class AcpClient:
    def __init__(self) -> None:
        # Pipe opencode's stderr to ours so we can see its logs.
        self._proc = subprocess.Popen(  # noqa: S603
            ["opencode", "acp"],  # noqa: S607 — opencode installed via npm in the container
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,  # forward opencode's own logs to our stderr
            bufsize=0,
        )
        self._responses: queue.Queue[dict] = queue.Queue()
        self._output_lines: list[str] = []
        self._stop_reason: str = ""
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        assert self._proc.stdout is not None  # noqa: S101 — guaranteed by stdout=PIPE
        for raw in self._proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON line from opencode stdout — log it for visibility.
                print(f"[acp] non-json: {line[:200]}", file=sys.stderr)
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
        elif kind in ("session_completed", "session_error"):
            print(f"[acp] session ended: {kind}", file=sys.stderr)
            self._stop_reason = kind
        else:
            # Log unknown notification kinds so we can spot unexpected events.
            if kind:
                print(f"[acp] notification: {kind}", file=sys.stderr)

    def send(self, req: dict, timeout: float = 15.0) -> dict:
        assert self._proc.stdin is not None  # noqa: S101 — guaranteed by stdin=PIPE
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

    print(f"[runner] task={TASK!r}  model={MODEL_ID}  timeout={TIMEOUT_SECONDS}s", file=sys.stderr)

    _preflight()
    _write_config()

    print("[runner] starting opencode acp ...", file=sys.stderr)
    client = AcpClient()
    _id = 0

    def req(method: str, params: dict, timeout: float = 15.0) -> dict:
        nonlocal _id
        _id += 1
        t0 = time.monotonic()
        result = client.send(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": _id},
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        status = "ok" if "error" not in result else f"error={result['error']}"
        print(f"[acp] {method} → {status} ({elapsed:.1f}s)", file=sys.stderr)
        return result

    # 1. Initialize
    init = req("initialize", {"protocolVersion": 1, "capabilities": {}})
    if "error" in init:
        print(f"[runner] ACP initialize failed: {init['error']}", file=sys.stderr)
        client.terminate()
        return 1

    # 2. Create session
    new_sess = req("session/new", {"cwd": CWD, "mcpServers": []})
    sid = new_sess.get("result", {}).get("sessionId", "")
    if not sid:
        print(f"[runner] session/new failed: {new_sess}", file=sys.stderr)
        client.terminate()
        return 1

    # 3. Resume (loads current model list; sets cwd context)
    req("session/resume", {"sessionId": sid, "cwd": CWD})

    # 4. Send prompt — give it a short window for the ack, then poll for the
    #    session_completed / session_error notification.
    #    Some opencode builds block on session/prompt (ack = completion);
    #    others ack immediately and complete via notification.
    print(
        f"[runner] sending prompt, polling for completion (max {TIMEOUT_SECONDS}s) ...",
        file=sys.stderr,
    )
    result = req(
        "session/prompt",
        {"sessionId": sid, "prompt": [{"type": "text", "text": TASK}]},
        timeout=30.0,  # short — just waiting for ack, not completion
    )

    # Blocking mode: RPC response carries stopReason.
    stop_reason = result.get("result", {}).get("stopReason", "")
    if stop_reason:
        if stop_reason != "end_turn":
            error = result.get("error", "")
            print(f"[runner] unexpected stop: {stop_reason or error}", file=sys.stderr)
            client.terminate()
            return 1
        client.terminate()
        return 0

    # Notification-driven mode: poll client._stop_reason until set or timeout.
    deadline = time.monotonic() + float(TIMEOUT_SECONDS)
    last_log = time.monotonic()
    while time.monotonic() < deadline:
        if client._stop_reason:
            break
        if time.monotonic() - last_log >= 30:
            elapsed = TIMEOUT_SECONDS - (deadline - time.monotonic())
            chunks = len(client._output_lines)
            print(
                f"[runner] waiting ... {elapsed:.0f}s elapsed, {chunks} chunks received",
                file=sys.stderr,
            )
            last_log = time.monotonic()
        time.sleep(0.5)

    stop_reason = client._stop_reason
    chunks_received = len(client._output_lines)
    print(
        f"[runner] finished: stop_reason={stop_reason!r}  chunks={chunks_received}",
        file=sys.stderr,
    )

    if stop_reason != "session_completed":
        print(
            f"[runner] unexpected stop: {stop_reason or 'timeout'}",
            file=sys.stderr,
        )
        client.terminate()
        return 1

    client.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
