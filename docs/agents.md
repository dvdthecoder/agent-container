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
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run  # no /v1 suffix
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
aider --yes --model <model> --map-tokens 1024 \
      --message "<task>" /workspace
# OPENAI_BASE_URL and OPENAI_API_KEY are set in the container environment
```

---

## OpenCode

[opencode](https://opencode.ai) is a multi-turn coding agent with a full tool-calling loop.
It can run commands, read files, write code, check errors, and iterate — more capable than
aider for complex tasks.

opencode v1.14+ calls the OpenAI Responses API (`POST /v1/responses`). A thin in-process
adapter translates this to Chat Completions and back — pure JSON reshaping, no model-specific
code. See [Architecture](architecture.md#opencode-adapter-thin-proxy) for the full proxy design
including SSE event sequence, adaptive `tool_choice`, and `parallel_tool_calls`.

The sandbox installs a pinned version (`opencode-ai@1.14.31`) so proxy-compatibility changes in
opencode do not silently break runs. Update the pin deliberately after verifying the proxy still
works end-to-end.

```bash
OPENAI_BASE_URL=https://your-org--agent-container-serve-serve.modal.run  # no /v1 suffix
OPENAI_API_KEY=modal
OPENCODE_MODEL=qwen2.5-coder-32b
OPENCODE_TOOL_CHOICE=auto   # optional: override first-turn tool_choice (default: required)
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

## aider vs opencode

Both backends use your self-hosted model on Modal — same GPU, same cost, same PR output. The
difference is in *how* they drive the model to produce code.

### How each one works

**aider** is single-shot. It builds a prompt that includes the task, a concise map of the repo
(file names and function signatures), and the contents of the files it judges most relevant.
It sends one request to the model and expects back a complete set of file edits in a structured
diff format. It applies the edits, commits them, and exits. One round-trip to the model.

**opencode** is iterative. It runs a tool-calling loop: read a file, write a file, run a
command, check the output, reason about what to do next. It can run `pytest`, inspect failures,
fix the code, and run again — all in one invocation. This requires the model to support function
calling reliably and to reason across multiple turns.

### When to use aider

- **Default choice.** Works with the `test` profile (Qwen2.5-Coder 32B on A100 80GB) — smallest,
  cheapest GPU.
- The task is well-specified: "add a `sum_to_n` function to `mathlib.py`", "fix the off-by-one
  in `paginate()`". The model knows exactly what to write.
- You want the fastest turnaround — one model call, one diff, done.
- You don't need the agent to run tests or check its own output.
- The target repo is small-to-medium — the repo map fits in the context window.

### When to use opencode

- The task involves a bug that requires diagnosis: "tests are failing, figure out why and fix
  it". opencode can run `pytest`, read the failure output, trace it to a root cause, and patch
  the right line.
- The task spans multiple files with non-obvious dependencies. opencode can explore the repo
  interactively before writing anything.
- You want the agent to verify its own work — run tests after editing, confirm green, then
  commit.
- You are using the `prod` or `minimax` profile (Qwen3-Coder 80B or MiniMax M2.5) where
  tool calling is reliable and the extra latency of a multi-turn loop is acceptable.

### Trade-offs at a glance

| | aider | opencode |
|--|-------|---------|
| Approach | Single-shot diff edit | Multi-turn tool-calling loop |
| Model calls per task | 1–2 | 5–20+ |
| Requires tool calling | No | Yes |
| Can run tests and iterate | No | Yes |
| Works on `test` profile (32B) | Yes | Possible but weaker |
| Recommended GPU profile | `test` or higher | `prod` or `minimax` |
| Cold start sensitivity | Low (fewer calls) | Higher (more round-trips) |
| Best task type | Targeted, well-specified edits | Debugging, multi-file, self-verifying |

### In practice

Start with aider. It covers most automated tasks — adding a function, applying a patch,
renaming a symbol. If you find the agent is producing empty diffs, missing context that
requires reading multiple files in sequence, or needs to verify its own output by running tests,
switch to opencode with the `prod` profile.

---

## Comparison (all backends)

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
