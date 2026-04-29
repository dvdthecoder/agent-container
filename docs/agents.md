# Agent Backends

Four coding agent backends can run inside the sandbox. All produce the same `AgentTaskResult`
— PR creation, the dashboard, and MCP integration work identically regardless of which backend
you use.

## Selecting a backend

```bash
agent-run run --backend aider    ...   # default
agent-run run --backend opencode ...
agent-run run --backend claude   ...
agent-run run --backend gemini   ...
```

Or in Python:

```python
spec = AgentTaskSpec(
    repo="https://github.com/org/myapp",
    task="Fix the login bug",
    backend="aider",
)
```

---

## aider (default)

[aider](https://aider.chat) is a coding agent that calls `/v1/chat/completions` directly —
no proxy, no translation layer. It uses text-based diff editing: the model returns a structured
diff and aider applies it to the workspace.

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder
```

**How it works:** aider sends the relevant file contents and task to the model and receives
back a structured edit in diff format. It applies the diff, commits the result, and exits.
No function calling required at the inference level — works reliably with any model.

**Best for:** the default setup. Fastest path to a working diff and PR. Works with any
OpenAI-compatible endpoint out of the box.

Invoked inside the container as:
```bash
aider --yes --no-git --model <model> \
      --openai-api-base <url> --openai-api-key <key> \
      --message "<task>" /workspace
```

---

## OpenCode

[opencode](https://opencode.ai) is a multi-turn coding agent with a full tool-calling loop.
It can run commands, read files, write code, check errors, and iterate — more capable than
aider for complex tasks.

opencode v1.14+ calls the OpenAI Responses API (`POST /v1/responses`). A thin adapter
translates this to Chat Completions and back — see [Architecture](architecture.md#opencode-adapter-thin-proxy).

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder
```

**Best for:** complex multi-step tasks where the agent needs to reason, run tests, and iterate.
Requires a model with reliable tool calling (use `prod` or `minimax` profile).

Invoked inside the container as:
```bash
python3 /opencode_runner.py "<task prompt>"
```

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
    Use `aider` or `opencode` for full air-gap with your self-hosted model.

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

---

## Comparison

| | aider | opencode | Claude Code | Gemini CLI |
|--|-------|---------|------------|-----------|
| Model | Self-hosted on Modal | Self-hosted on Modal | Anthropic API | Google AI / Vertex |
| External API key | ❌ | ❌ | ✅ | ✅ |
| Air-gap capable | ✅ | ✅ | ❌ | ✅ (Vertex) |
| Proxy needed | ❌ | ✅ (thin adapter) | ❌ | ❌ |
| Tool calling required | ❌ (diff format) | ✅ | ✅ | ✅ |
| Multi-turn reasoning | ❌ | ✅ | ✅ | ✅ |
| Default | ✅ | ❌ | ❌ | ❌ |

---

## Stub backend (testing only)

```bash
AGENT_BACKEND=stub
```

Used in integration tests. Echoes the task prompt to stdout and exits 0 without calling any
model or making code changes. Exercises the full sandbox lifecycle at near-zero cost.
