"""Non-interactive opencode runner — Responses API → Chat Completions proxy.

Usage: python3 opencode_runner.py <task>

Environment variables read:
  OPENAI_BASE_URL  — vLLM endpoint, already normalised to include /v1
                     (e.g. https://host/v1).  Set by SandboxConfig.env_for_backend.
  OPENAI_API_KEY   — API key for the above endpoint
  OPENCODE_MODEL   — model name as served by vLLM (e.g. qwen2.5-coder)
  OPENCODE_WORKDIR — working directory inside the sandbox (default: /workspace)
  OPENCODE_TIMEOUT — seconds before the runner gives up (default: 300)

Architecture:
  opencode v1.14+ calls POST /v1/responses (OpenAI Responses API).
  vLLM serves POST /v1/chat/completions (Chat Completions API).

  This runner starts a thin in-process proxy on localhost that translates
  between the two formats, then points opencode at it via config.json.

  The proxy is a pure format adapter — no model-specific logic:
    Request:  Responses API input + tools  →  Chat Completions messages + tools
    Response: Chat Completions tool_calls  →  Responses API function_call items
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
# SandboxConfig.env_for_backend already normalises to include /v1.
# Strip it here so we can construct endpoint URLs explicitly.
_raw_base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
BASE_URL = _raw_base[:-3] if _raw_base.endswith("/v1") else _raw_base
API_KEY = os.environ.get("OPENAI_API_KEY", "")
RAW_MODEL = os.environ.get("OPENCODE_MODEL", "")
MODEL_ID = (
    f"openai/{RAW_MODEL}" if RAW_MODEL and "/" not in RAW_MODEL else RAW_MODEL or "openai/unknown"
)
TIMEOUT_SECONDS = int(os.environ.get("OPENCODE_TIMEOUT", "300"))
# "required" forces the model to call a tool each turn, preventing small models
# from replying with plain text and producing an empty diff.  Set
# OPENCODE_TOOL_CHOICE=auto to revert to the default for specific deployments.
TOOL_CHOICE = os.environ.get("OPENCODE_TOOL_CHOICE", "required")

_PROXY_PORT = 8080


# ---------------------------------------------------------------------------
# Format conversion helpers
# ---------------------------------------------------------------------------


def _convert_tools(tools: list) -> list:
    """Convert Responses API tool definitions to Chat Completions format.

    Responses API:    {"type": "function", "name": "x", "parameters": {}}
    Chat Completions: {"type": "function", "function": {"name": "x", "parameters": {}}}
    """
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and "function" not in t:
            out.append(
                {"type": "function", "function": {k: v for k, v in t.items() if k != "type"}}
            )
        else:
            out.append(t)
    return out


def _convert_input_items(items: list) -> list:
    """Convert Responses API input items to Chat Completions messages.

    Handles:
      - Plain message dicts (role + content)
      - tool_result items  → role: tool  (Chat Completions standard)
      - function_call items → assistant message with tool_calls
      - String items
    """
    messages = []
    for item in items:
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            continue

        item_type = item.get("type", "")
        role = item.get("role", "user")

        # opencode sends the newer OpenAI 'developer' role.
        # Chat Completions only accepts system/user/assistant/tool — map it.
        if role == "developer":
            role = "system"

        if item_type == "tool_result":
            # Responses API tool result → Chat Completions tool message.
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": str(item.get("output", "")),
                }
            )
        elif item_type == "function_call":
            # Responses API function_call → assistant message with tool_calls.
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": item.get("call_id", item.get("id", "")),
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": item.get("arguments", "{}"),
                            },
                        }
                    ],
                }
            )
        elif item_type in ("message", ""):
            content = item.get("content", "")
            if isinstance(content, list):
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                messages.append({"role": role, "content": text})
            else:
                messages.append({"role": role, "content": content})
        else:
            messages.append({"role": role, "content": json.dumps(item)})

    return messages


# ---------------------------------------------------------------------------
# Proxy handler
# ---------------------------------------------------------------------------


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    """Translate Responses API calls to Chat Completions for vLLM."""

    target: str = ""  # set to BASE_URL before starting the server

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102
        pass  # suppress default access log

    def do_GET(self) -> None:  # noqa: N802
        self._forward(method="GET", body=None)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        if self.path in ("/v1/responses", "/responses"):
            self._handle_responses(body)
        else:
            self._forward(method="POST", body=body)

    def _handle_responses(self, body: bytes) -> None:
        try:
            req = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad request"}')
            return

        tools_count = len(req.get("tools", []))
        input_type = type(req.get("input", "")).__name__
        print(
            f"[proxy] request: input_type={input_type}"
            f" tools={tools_count} stream={req.get('stream')}",
            file=sys.stderr,
        )

        raw_input = req.get("input", "")
        if isinstance(raw_input, str):
            messages = [{"role": "user", "content": raw_input}]
        elif isinstance(raw_input, list):
            messages = _convert_input_items(raw_input)
        else:
            messages = [{"role": "user", "content": str(raw_input)}]

        chat_req: dict = {
            "model": RAW_MODEL,
            "messages": messages,
            "stream": bool(req.get("stream", False)),
        }

        # Pass tools in the standard Chat Completions tools field.
        # vLLM handles these correctly with --enable-auto-tool-choice.
        # Default tool_choice is "required" so the model must call a tool rather
        # than replying with plain text — without this, small models like Qwen 7B
        # sometimes respond conversationally and make no file edits.
        # Override via OPENCODE_TOOL_CHOICE=auto for specific deployments.
        if req.get("tools"):
            chat_req["tools"] = _convert_tools(req["tools"])
            chat_req["tool_choice"] = TOOL_CHOICE

        for key in ("temperature", "max_tokens"):
            if key in req:
                chat_req[key] = req[key]

        stream = chat_req["stream"]
        chat_body = json.dumps(chat_req).encode()
        url = f"{self.target}/v1/chat/completions"
        print(
            f"[proxy] → chat/completions  stream={stream}"
            f"  tools={len(chat_req.get('tools', []))}  messages={len(messages)}",
            file=sys.stderr,
        )

        _request_timeout = min(120, TIMEOUT_SECONDS)
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
            with urllib.request.urlopen(http_req, timeout=_request_timeout) as resp:  # noqa: S310
                if stream:
                    self._stream_chat_to_responses(resp)
                else:
                    translated = self._translate_chat_response(resp.read())
                    self._send(200, translated)
        except urllib.error.HTTPError as e:
            body_err = e.read()
            print(
                f"[proxy] upstream {e.code} ({url}):\n{body_err.decode('utf-8', errors='replace')}",
                file=sys.stderr,
            )
            self._send(e.code, body_err)
        except TimeoutError as exc:
            msg = f"upstream timeout after {_request_timeout}s"
            print(f"[proxy] {msg}: {exc}", file=sys.stderr)
            self._send(504, json.dumps({"error": msg}).encode())
        except Exception as exc:  # noqa: BLE001
            print(f"[proxy] upstream exception: {exc}", file=sys.stderr)
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def _translate_chat_response(self, raw: bytes) -> bytes:
        """Convert a Chat Completions response to Responses API format."""
        try:
            chat = json.loads(raw)
        except json.JSONDecodeError:
            return raw

        message = chat.get("choices", [{}])[0].get("message", {})
        text = message.get("content") or ""
        native_tool_calls = message.get("tool_calls") or []

        preview = text[:300] if text else "<empty>"
        print(
            f"[proxy] ← response: tool_calls={len(native_tool_calls)}"
            f" text={len(text)}chars preview={preview!r}",
            file=sys.stderr,
        )

        output = []
        if text:
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
        for tc in native_tool_calls:
            fn = tc.get("function", {})
            call_id = tc.get("id", "")
            output.append(
                {
                    "type": "function_call",
                    "id": call_id,
                    "call_id": call_id,
                    "name": fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                }
            )

        if not output:
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            )

        return json.dumps(
            {
                "id": chat.get("id", "resp_proxy"),
                "object": "response",
                "model": chat.get("model", RAW_MODEL),
                "output": output,
                "usage": chat.get("usage", {}),
            }
        ).encode()

    def _stream_chat_to_responses(self, resp: object) -> None:
        """Stream Chat Completions SSE and re-emit as Responses API events."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        full_text: list[str] = []
        # index → accumulated tool call chunk
        tool_calls_buf: dict[int, dict] = {}

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

            delta = chunk.get("choices", [{}])[0].get("delta", {})
            text_delta = delta.get("content") or ""
            if text_delta:
                full_text.append(text_delta)
                self._write_sse(
                    json.dumps({"type": "response.output_text.delta", "delta": text_delta})
                )

            for tc_delta in delta.get("tool_calls") or []:
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls_buf:
                    tool_calls_buf[idx] = {"id": "", "name": "", "arguments": ""}
                buf = tool_calls_buf[idx]
                if tc_delta.get("id"):
                    buf["id"] = tc_delta["id"]
                fn = tc_delta.get("function", {})
                if fn.get("name"):
                    buf["name"] += fn["name"]
                if fn.get("arguments"):
                    buf["arguments"] += fn["arguments"]

        output = []
        text = "".join(full_text)
        if text:
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
        for idx in sorted(tool_calls_buf):
            buf = tool_calls_buf[idx]
            call_id = buf["id"]
            output.append(
                {
                    "type": "function_call",
                    "id": call_id,
                    "call_id": call_id,
                    "name": buf["name"],
                    "arguments": buf["arguments"],
                }
            )

        if not output:
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            )

        self._write_sse(
            json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_proxy",
                        "object": "response",
                        "model": RAW_MODEL,
                        "output": output,
                    },
                }
            )
        )
        self.wfile.flush()

    def _write_sse(self, data: str) -> None:
        self.wfile.write(f"data: {data}\n\n".encode())

    def _forward(self, method: str, body: bytes | None) -> None:
        url = f"{self.target}{self.path}"
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")
        }
        headers.setdefault("Authorization", f"Bearer {API_KEY}")
        http_req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(http_req, timeout=30) as resp:  # noqa: S310
                raw = resp.read()
                ct = resp.headers.get("Content-Type", "application/json")
                self._send(resp.status, raw, content_type=ct)
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read())
        except Exception as exc:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def _send(self, code: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_proxy(target_base_url: str) -> None:
    """Start the Responses → Chat Completions proxy in a background thread."""
    _ProxyHandler.target = target_base_url
    server = http.server.ThreadingHTTPServer(("127.0.0.1", _PROXY_PORT), _ProxyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"[proxy] started on 127.0.0.1:{_PROXY_PORT} → {target_base_url}", file=sys.stderr)


# ---------------------------------------------------------------------------
# opencode config
# ---------------------------------------------------------------------------


def _write_config() -> None:
    """Write ~/.config/opencode/config.json pointing at the local proxy."""
    if not RAW_MODEL or "/" in RAW_MODEL:
        return

    proxy_url = f"http://127.0.0.1:{_PROXY_PORT}"
    cfg = {
        "model": MODEL_ID,
        "provider": {
            "openai": {
                "options": {"apiKey": API_KEY, "baseURL": proxy_url},
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
        self._proc = subprocess.Popen(  # noqa: S603
            ["opencode", "acp"],  # noqa: S607
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
            raw = update.get("content", "")
            # opencode may send content as a plain string or as a structured
            # dict {"type": "text", "text": "..."} / list of such parts.
            if isinstance(raw, dict):
                chunk = raw.get("text", "")
            elif isinstance(raw, list):
                chunk = "".join(p.get("text", "") for p in raw if isinstance(p, dict))
            else:
                chunk = str(raw) if raw else ""
            if chunk:
                self._output_lines.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()
        elif kind in ("session_completed", "session_error"):
            print(f"[acp] session ended: {kind}", file=sys.stderr)
            self._stop_reason = kind
        elif kind:
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
        except Exception:  # noqa: BLE001, S110
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not TASK:
        print("Usage: opencode_runner.py <task>", file=sys.stderr)
        return 1

    print(
        f"[runner] task={TASK!r}  model={MODEL_ID}  workdir={CWD}  timeout={TIMEOUT_SECONDS}s",
        file=sys.stderr,
    )

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

    init = req("initialize", {"protocolVersion": 1, "capabilities": {}})
    if "error" in init:
        print(f"[runner] ACP initialize failed: {init['error']}", file=sys.stderr)
        client.terminate()
        return 1

    new_sess = req("session/new", {"cwd": CWD, "mcpServers": []})
    sid = new_sess.get("result", {}).get("sessionId", "")
    if not sid:
        print(f"[runner] session/new failed: {new_sess}", file=sys.stderr)
        client.terminate()
        return 1

    req("session/resume", {"sessionId": sid, "cwd": CWD})

    print(
        f"[runner] sending prompt, polling for completion (max {TIMEOUT_SECONDS}s) ...",
        file=sys.stderr,
    )
    result = req(
        "session/prompt",
        {"sessionId": sid, "prompt": [{"type": "text", "text": TASK}]},
        timeout=30.0,
    )

    stop_reason = result.get("result", {}).get("stopReason", "")
    if stop_reason:
        if stop_reason != "end_turn":
            print(
                f"[runner] unexpected stop: {stop_reason or result.get('error', '')}",
                file=sys.stderr,
            )
            client.terminate()
            return 1
        client.terminate()
        return 0

    deadline = time.monotonic() + float(TIMEOUT_SECONDS)
    last_log = time.monotonic()
    while time.monotonic() < deadline:
        if client._stop_reason:
            break
        if time.monotonic() - last_log >= 30:
            elapsed = TIMEOUT_SECONDS - (deadline - time.monotonic())
            print(
                f"[runner] waiting ... {elapsed:.0f}s elapsed,"
                f" {len(client._output_lines)} chunks received",
                file=sys.stderr,
            )
            last_log = time.monotonic()
        time.sleep(0.5)

    stop_reason = client._stop_reason
    print(
        f"[runner] finished: stop_reason={stop_reason!r}  chunks={len(client._output_lines)}",
        file=sys.stderr,
    )

    if stop_reason != "session_completed":
        print(f"[runner] unexpected stop: {stop_reason or 'timeout'}", file=sys.stderr)
        client.terminate()
        return 1

    client.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
