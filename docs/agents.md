# Agent Backends

Three coding agents can run inside the sandbox. All produce the same `AgentTaskResult` — PR
creation, the dashboard, and MCP integration work identically regardless of which backend you use.

## Selecting a backend

```bash
agent-run --backend opencode ...   # default
agent-run --backend claude  ...
agent-run --backend gemini  ...
```

Or in Python:

```python
spec = AgentTaskSpec(
    repo="https://github.com/org/myapp",
    task="Fix the login bug",
    backend="claude",
)
```

---

## OpenCode (default)

[OpenCode](https://opencode.ai) is an open-source coding agent that runs inside the sandbox and
talks to your self-hosted model endpoint on Modal.

```bash
AGENT_BACKEND=opencode   # or omit — this is the default

# Set after deploying modal/serve.py:
OPENAI_BASE_URL=https://your-org--agent-container-serve.modal.run/v1
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen3-coder   # or minimax-m2.5
```

**Best for**: the default setup. Everything stays on Modal — no external API keys, no code leaves
your infrastructure.

Invoked inside the container as:
```bash
python3 /opencode_runner.py "<task prompt>"
```

`opencode_runner.py` drives opencode non-interactively via its ACP (Agent Client Protocol)
JSON-RPC interface (`opencode acp`), streaming output to stdout as the agent works.

---

## Claude Code CLI

Anthropic's official [Claude Code](https://claude.ai/claude-code) CLI runs inside the container.

```bash
AGENT_BACKEND=claude
ANTHROPIC_API_KEY=sk-ant-...
```

Invoked inside the container as:
```bash
claude --print "<task prompt>"
```

!!! warning "Privacy note"
    Claude Code sends prompts to Anthropic's API. Code context leaves your Modal sandbox.
    Use the `opencode` backend for full air-gap.

---

## Gemini CLI

Google's [Gemini CLI](https://github.com/google-gemini/gemini-cli) runs inside the container.

```bash
AGENT_BACKEND=gemini
GEMINI_API_KEY=...
# or for Vertex AI (GCP)
GOOGLE_CLOUD_PROJECT=...
```

Invoked inside the container as:
```bash
gemini --yolo -p "<task prompt>"
```

!!! note "Air-gap with Vertex AI"
    Using Vertex AI keeps prompts within your GCP project. For full on-prem air-gap, use the
    `opencode` backend with your self-hosted Modal endpoint.

---

## Comparison

| | OpenCode | Claude Code | Gemini CLI |
|---|---|---|---|
| Model | Self-hosted on Modal | Anthropic API | Google AI / Vertex |
| External API key needed | ❌ | ✅ | ✅ |
| Air-gap capable | ✅ | ❌ | ✅ (Vertex AI) |
| Default | ✅ | ❌ | ❌ |

---

## Stub backend (testing only)

```bash
AGENT_BACKEND=stub
```

Used in integration tests. Echoes the task prompt to stdout and exits 0 without calling any model
or making any code changes. Exercises the full sandbox lifecycle at near-zero cost.
