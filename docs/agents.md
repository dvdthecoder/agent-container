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

[OpenCode](https://opencode.ai) is an open-source coding agent designed to work with any
OpenAI-compatible model endpoint.

```bash
AGENT_BACKEND=opencode   # or omit — this is the default
OPENAI_BASE_URL=...
OPENAI_API_KEY=...
OPENCODE_MODEL=...
```

**Best for**: teams wanting the best quality-to-cost ratio. Point `OPENAI_BASE_URL` at DeepSeek's
API (`deepseek-v4-pro`) for ~74% aider score at ~$1–3/run, or at a self-hosted SGLang endpoint
for full air-gap deployments. The model is fully under your control.

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
    Claude Code sends prompts to Anthropic's API. Code context leaves your network.
    Use the `opencode` backend with SGLang if full air-gap is required.

**Best for**: teams already using Claude Code who want consistent agent behaviour between their
local sessions and automated sandbox runs.

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

**Best for**: teams already on GCP who need to stay within Google's infrastructure. Vertex AI
backend keeps prompts within your GCP project.

---

## Comparison

| | OpenCode | Claude Code | Gemini CLI |
|---|---|---|---|
| Model | Any via `OPENAI_BASE_URL` | Anthropic API | Google AI / Vertex |
| Open source | ✅ | ❌ | ✅ |
| Air-gap capable | ✅ (with SGLang or MiniMax) | ❌ | ✅ (Vertex AI) |
| Recommended model | deepseek-v4-pro (~74% aider) | Claude Sonnet 4.5 | Gemini 2.5 Pro |

---

## Stub backend (testing only)

```bash
AGENT_BACKEND=stub
```

Used in integration tests. Echoes the task prompt to stdout and exits 0 without calling any model
or making any code changes. Exercises the full sandbox lifecycle at near-zero cost.
