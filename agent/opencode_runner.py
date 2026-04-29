"""Non-interactive opencode runner via ACP (Agent Client Protocol).

Usage: python3 opencode_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — custom OpenAI-compatible base URL (e.g. SGLang on Modal)
  OPENAI_API_KEY   — API key for the above endpoint
  OPENCODE_MODEL   — model name; if it contains '/' it's used as-is (e.g.
                     'github-models/deepseek/deepseek-v3-0324'); otherwise
                     it is prefixed with 'openai/' and mapped to the custom
                     base URL above.
  OPENCODE_WORKDIR — working directory inside the sandbox (default: /workspace)
"""

from __future__ import annotations

import http.server
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
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
RAW_MODEL = os.environ.get("OPENCODE_MODEL", "")

# Strip trailing /v1 — we add it explicitly where needed.
if BASE_URL.endswith("/v1"):
    BASE_URL = BASE_URL[:-3]

# Resolve the model to provider/model format.
if "/" in RAW_MODEL:
    MODEL_ID = RAW_MODEL
else:
    MODEL_ID = f"openai/{RAW_MODEL}" if RAW_MODEL else "opencode/big-pickle"

TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_TIMEOUT", "300"))

# Port the local Responses→ChatCompletions proxy listens on.
_PROXY_PORT = 8080


# ---------------------------------------------------------------------------
# Responses API → Chat Completions proxy
#
# opencode v1.14+ calls POST /v1/responses (OpenAI Responses API).
# SGLang only serves POST /v1/chat/completions.
# This proxy intercepts /v1/responses, translates the request/response, and
# forwards everything else unchanged.
# ---------------------------------------------------------------------------


def _convert_tools(tools: list) -> list:
    """Convert Responses API tool definitions to Chat Completions format.

    Responses API:   {"type":"function","name":"x","parameters":{}}
    Chat Completions: {"type":"function","function":{"name":"x","parameters":{}}}
    """
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and "function" not in t:
            # Responses API format — wrap in function key.
            out.append(
                {
                    "type": "function",
                    "function": {k: v for k, v in t.items() if k != "type"},
                }
            )
        else:
            # Already in Chat Completions format or unknown — pass through.
            out.append(t)
    return out


def _convert_input_items(items: list) -> list:
    """Convert Responses API input items to Chat Completions messages.

    Handles plain message dicts, tool_result items, and string items.
    """
    messages = []
    for item in items:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue
        item_type = item.get("type", "")
        role = item.get("role", "user")
        if item_type == "tool_result":
            # Responses API tool result → Chat Completions tool message.
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("tool_use_id", ""),
                    "content": str(item.get("output", "")),
                }
            )
        elif item_type in ("message", ""):
            content = item.get("content", "")
            if isinstance(content, list):
                # content blocks — flatten to text for simplicity.
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                messages.append({"role": role, "content": text})
            else:
                messages.append({"role": role, "content": content})
        else:
            # Unknown type — best effort.
            messages.append({"role": role, "content": json.dumps(item)})
    return messages


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Translate Responses API calls to Chat Completions for SGLang."""

    target: str = ""  # set to BASE_URL before starting

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        pass  # suppress default access log

    # ── routing ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        self._forward(method="GET", body=None)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if self.path in ("/v1/responses", "/responses"):
            self._handle_responses(body)
        else:
            self._forward(method="POST", body=body)

    # ── responses → chat completions ─────────────────────────────────────────

    def _handle_responses(self, body: bytes) -> None:
        try:
            req = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad request"}')
            return

        # Log request shape for debugging (truncated, no content).
        tools_count = len(req.get("tools", []))
        input_type = type(req.get("input", "")).__name__
        print(
            f"[proxy] request: input_type={input_type} tools={tools_count}"
            f" stream={req.get('stream')}",
            file=sys.stderr,
        )

        # Convert Responses API input to Chat Completions messages list.
        raw_input = req.get("input", "")
        if isinstance(raw_input, str):
            messages = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            messages = _convert_input_items(raw_input)
        else:
            messages = [{"role": "user", "content": str(raw_input)}]

        chat_req: dict = {
            # Always use the bare model name (e.g. "qwen2.5-coder") — never
            # forward opencode's provider-prefixed string ("openai/qwen2.5-coder")
            # to SGLang, which only recognises its --served-model-name value.
            "model": RAW_MODEL,
            "messages": messages,
            "stream": bool(req.get("stream", False)),
        }
        for key in ("temperature", "max_tokens"):
            if key in req:
                chat_req[key] = req[key]

        # Forward tools in Chat Completions format.
        # SGLang must be launched with --tool-call-parser and
        # --enable-auto-tool-choice for these to work (see modal/serve.py).
        if req.get("tools"):
            chat_req["tools"] = _convert_tools(req["tools"])
            if req.get("tool_choice"):
                chat_req["tool_choice"] = req["tool_choice"]

        stream = chat_req["stream"]
        chat_body = json.dumps(chat_req).encode()

        url = f"{self.target}/v1/chat/completions"
        print(
            f"[proxy] → chat/completions  stream={stream}"
            f"  tools={len(chat_req.get('tools', []))}  messages={len(messages)}",
            file=sys.stderr,
        )

        http_req = urllib.request.Request(  # noqa: S310
            url,
            data=chat_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_req, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                if stream:
                    self._stream_chat_to_responses(resp)
                else:
                    raw = resp.read()
                    translated = self._translate_chat_response(raw)
                    self._send(200, translated, content_type="application/json")
        except urllib.error.HTTPError as e:
            body_err = e.read()
            # Decode for readability — SGLang may return JSON error detail.
            body_str = body_err.decode("utf-8", errors="replace")[:400]
            print(f"[proxy] upstream {e.code}: {body_str}", file=sys.stderr)
            self._send(e.code, body_err)
        except Exception as exc:
            print(f"[proxy] upstream exception: {exc}", file=sys.stderr)
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def _translate_chat_response(self, raw: bytes) -> bytes:
        """Convert a chat completions response to Responses API format."""
        try:
            chat = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        text = chat.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        resp = {
            "id": chat.get("id", "resp_proxy"),
            "object": "response",
            "model": chat.get("model", RAW_MODEL),
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "usage": chat.get("usage", {}),
        }
        return json.dumps(resp).encode()

    def _stream_chat_to_responses(self, resp: object) -> None:
        """Stream chat completions SSE and re-emit as Responses API events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        full_text: list[str] = []
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content") or ""
            if delta:
                full_text.append(delta)
                event = json.dumps({"type": "response.output_text.delta", "delta": delta})
                self._write_sse(event)

        # Emit completed event.
        text = "".join(full_text)
        completed = json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_proxy",
                    "object": "response",
                    "model": RAW_MODEL,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    ],
                },
            }
        )
        self._write_sse(completed)
        self.wfile.flush()

    def _write_sse(self, data: str) -> None:
        line = f"data: {data}\n\n".encode()
        self.wfile.write(line)

    # ── plain forward ─────────────────────────────────────────────────────────

    def _forward(self, method: str, body: bytes | None) -> None:
        url = f"{self.target}{self.path}"
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")
        }
        headers.setdefault("Authorization", f"Bearer {API_KEY}")
        http_req = urllib.request.Request(  # noqa: S310
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(http_req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                ct = resp.headers.get("Content-Type", "application/json")
                self._send(resp.status, raw, content_type=ct)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as exc:
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_proxy(target_base_url: str) -> None:
    """Start the Responses→ChatCompletions proxy in a background thread."""
    _ProxyHandler.target = target_base_url
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _PROXY_PORT), _ProxyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[proxy] started on 127.0.0.1:{_PROXY_PORT} → {target_base_url}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Pre-flight: verify the model endpoint is reachable
# ---------------------------------------------------------------------------


# How long to wait for a cold serve to become ready before giving up.
# SGLang on A10G takes ~3 min to load Qwen2.5-Coder-7B from the volume.
_SERVE_COLDSTART_BUDGET = int(os.environ.get("SERVE_COLDSTART_BUDGET", "300"))
_SERVE_POLL_INTERVAL = 10  # seconds between probe attempts


def _wait_for_serve() -> bool:
    """Block until BASE_URL/v1/models returns 200, or the cold-start budget expires.

    Returns True if the endpoint is ready, False if it never came up.
    Logs each probe attempt so progress is visible in real time.
    """
    if not BASE_URL:
        print(
            "[preflight] OPENAI_BASE_URL is not set — using built-in model",
            file=sys.stderr,
        )
        return True

    url = f"{BASE_URL}/v1/models"
    deadline = time.monotonic() + _SERVE_COLDSTART_BUDGET
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1
        elapsed = int(time.monotonic() - (deadline - _SERVE_COLDSTART_BUDGET))
        print(
            f"[preflight] attempt {attempt}  elapsed={elapsed}s  url={url}",
            file=sys.stderr,
        )
        try:
            http_req = urllib.request.Request(  # noqa: S310
                url, headers={"Authorization": f"Bearer {API_KEY}"}
            )
            with urllib.request.urlopen(http_req, timeout=20) as resp:  # noqa: S310
                body = resp.read().decode("utf-8", errors="replace")
                print(
                    f"[preflight] serve ready ({resp.status}): {body[:200]}",
                    file=sys.stderr,
                )
                return True
        except urllib.error.HTTPError as e:
            print(f"[preflight] HTTP {e.code} — retrying", file=sys.stderr)
        except Exception as exc:
            print(f"[preflight] {exc} — retrying in {_SERVE_POLL_INTERVAL}s", file=sys.stderr)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_SERVE_POLL_INTERVAL, remaining))

    print(
        f"[preflight] serve not ready after {_SERVE_COLDSTART_BUDGET}s — aborting",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# Write opencode config
# ---------------------------------------------------------------------------


def _write_config() -> None:
    """Write ~/.config/opencode/config.json pointing at the local proxy."""
    if not BASE_URL or not API_KEY or not RAW_MODEL or "/" in RAW_MODEL:
        return

    # Point opencode at the local proxy (port 8080) instead of SGLang directly.
    # The proxy translates POST /v1/responses → POST /v1/chat/completions.
    proxy_url = f"http://127.0.0.1:{_PROXY_PORT}"

    cfg = {
        "model": MODEL_ID,
        "provider": {
            "openai": {
                "options": {
                    "apiKey": API_KEY,
                    "baseURL": proxy_url,
                },
                "models": {
                    RAW_MODEL: {
                        "name": RAW_MODEL,
                        "tool_call": True,
                        "temperature": True,
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
    print(f"[config] wrote opencode config: model={MODEL_ID}  proxy={proxy_url}", file=sys.stderr)


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
            stderr=sys.stderr,
            bufsize=0,
        )
        self._responses: queue.Queue[dict] = queue.Queue()
        self._output_lines: list[str] = []
        self._stop_reason: str = ""
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        assert self._proc.stdout is not None  # noqa: S101
        for raw in self._proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
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
            if kind:
                print(f"[acp] notification: {kind}", file=sys.stderr)

    def send(self, req: dict, timeout: float = 15.0) -> dict:
        assert self._proc.stdin is not None  # noqa: S101
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
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not TASK:
        print("Usage: opencode_runner.py <task>", file=sys.stderr)
        return 1

    print(f"[runner] task={TASK!r}  model={MODEL_ID}  timeout={TIMEOUT_SECONDS}s", file=sys.stderr)

    if not _wait_for_serve():
        return 1

    if BASE_URL:
        _start_proxy(BASE_URL)

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

    # 3. Resume
    req("session/resume", {"sessionId": sid, "cwd": CWD})

    # 4. Send prompt and wait for completion.
    print(
        f"[runner] sending prompt, polling for completion (max {TIMEOUT_SECONDS}s) ...",
        file=sys.stderr,
    )
    result = req(
        "session/prompt",
        {"sessionId": sid, "prompt": [{"type": "text", "text": TASK}]},
        timeout=30.0,
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

    # Notification-driven mode: poll until session_completed or timeout.
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
