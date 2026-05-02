# Roadmap

Current plan and prioritised backlog. Updated as phases complete and new work is scoped.

---

## Completed

### Phase 1 — vLLM + aider (done)
Direct Chat Completions pipeline, end-to-end PR creation.

- Replace SGLang with vLLM in `modal/serve.py`
- Add aider backend — no proxy, direct `/v1/chat/completions`
- Fix WARMING phase — poll inference endpoint before booting sandbox
- Fix URL normalisation — `SandboxConfig.env_for_backend()` owns per-backend env vars
- Fix sandbox termination — `terminate(wait=True)` blocks until container confirmed stopped
- Fix agent timeout — raise `TimeoutError` to prevent `collect_diff` on dead container
- Fix repo map — `--map-tokens 1024` so model knows what files exist
- Fix `__pycache__` in diffs — write `.git/info/exclude` after clone

### Phase 2 — Clean opencode proxy (done)
Pure Responses API ↔ Chat Completions adapter, zero model-specific code.

- Rewrite `opencode_runner.py` — 389 lines removed, 170 added
- Remove all SGLang-specific hacks (Qwen-native text injection, `<tool_call>` parsing)
- Tools passed in standard `tools` field — vLLM handles natively
- Emit full Responses API SSE event sequence per tool call (`response.output_item.added`,
  `response.function_call_arguments.done`, `response.output_item.done`) — required for opencode's
  agentic loop to detect and execute tool calls
- `parallel_tool_calls: false` — forces one tool call per turn so `read` result is visible
  before `edit` generates `oldString`
- Adaptive `tool_choice`: `required` until first `edit`/`write` call, then `auto` — prevents
  infinite bash loop after the file is written
- `make example BACKEND=opencode` produces a real diff and opens a PR (verified: fixture PR #12)

### Phase 3 — SGLang validation (done)
SGLang deployed as a separate Modal app (`agent-container-serve-sglang`); tool calling confirmed working with `hermes` parser.

- Add `sglang` profile to `serve.py` — separate app, same model/GPU as `test`
- Fix Modal env var injection — `SERVE_PROFILE` baked into secret at deploy time
- Fix SGLang image — switch to `nvidia/cuda:12.4.1-devel-ubuntu22.04` + `libnuma1` so JIT kernel compilation succeeds on A10G
- Fix warmup polling — `poll_interval` 5s → 30s to avoid flooding Modal with queued requests
- Fix tool-call parser — `qwen`/`qwen25` hangs on first tool-schema request; `hermes` resolves in 3s
- `make example BACKEND=opencode` against SGLang endpoint: tool call with 10 tools → ok (3.0s), PR opened

---

## In progress

| Task |
|------|
| Fix fixture repo — add `.gitignore` for `__pycache__` so pyc files stop appearing in PR diffs |

---

## Planned

### Phase 4 — Model expansion + observability
Broaden the model menu and make runs observable beyond the final PR.

| Issue | Task |
|---|---|
| [#113](https://github.com/dvdthecoder/agent-container/issues/113) | Add Qwen3-Coder and Gemma 4 model profiles to `serve.py` (vLLM) |
| [#114](https://github.com/dvdthecoder/agent-container/issues/114) | Docs: cost and quality comparison — self-hosted LLMs vs Claude API |
| [#115](https://github.com/dvdthecoder/agent-container/issues/115) | Docs: step-by-step guide for adding a new model profile |
| [#112](https://github.com/dvdthecoder/agent-container/issues/112) | Install opencode-monitor in sandbox — structured per-tool-call events in logs |
| [#111](https://github.com/dvdthecoder/agent-container/issues/111) | Stream real-time agent progress in CLI during RUNNING phase |

### Phase 5 — Production hardening
Close the gap between the current implementation and a fully team-deployed system.

| Issue | Task |
|---|---|
| [#107](https://github.com/dvdthecoder/agent-container/issues/107) | Warm sandboxes via Modal snapshot API — eliminate cold-start clone+install latency |
| [#108](https://github.com/dvdthecoder/agent-container/issues/108) | Deeper verification loop — Sentry errors, metrics, visual screenshots for frontend |

### Phase 6 — Team and integrations
Broader entry points and collaboration features.

| Issue | Task |
|---|---|
| [#109](https://github.com/dvdthecoder/agent-container/issues/109) | Slack bot — fire runs from Slack with automatic repo detection |
| — | Session persistence — pause, inspect, resume a run mid-flight |
| — | Multiplayer sessions — multiple engineers in a single live session |

---

## Known gaps vs production

See [Team Setup → Production gaps](teams.md#production-gaps-and-roadmap) for a detailed comparison
with how teams like Ramp run similar systems at scale.
