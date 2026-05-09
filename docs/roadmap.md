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

### Phase 2.5 — Hardening + observability (done)
Proxy correctness, per-phase timeouts, CI guards, test coverage, token tracking.

- [#64](https://github.com/dvdthecoder/agent-container/issues/64) Per-phase timeouts: `timeout_coldstart` / `timeout_agent` / `timeout_tests` replace the single `timeout_seconds` — each phase has its own budget; Modal sandbox lifetime = sum of all three
- [#65](https://github.com/dvdthecoder/agent-container/issues/65) Warmup probe uses `timeout_coldstart` budget — cold-start burns are isolated from agent execution time
- [#67](https://github.com/dvdthecoder/agent-container/issues/67) 24 unit tests for Responses API proxy — `_convert_tools`, `_convert_input_items`, `_translate_chat_response`, `_stream_chat_to_responses`, full SSE event sequence
- CI guard: `scripts/check_container_imports.py` blocks dev-only packages (`pytest`, `ruff`, etc.) from being imported in `modal/` or `agent/` at commit time
- opencode pinned to `1.14.31` — deliberate upgrade path with proxy compatibility check
- [#130](https://github.com/dvdthecoder/agent-container/issues/130) Token usage tab: proxy accumulates `usage` from every Chat Completions turn; persisted to SQLite; dashboard **Tokens** tab shows per-run prompt/completion/total tokens with live cost estimate and backend/date filters
- [#132](https://github.com/dvdthecoder/agent-container/issues/132) aider token capture: parses `Tokens: X sent, Y received.` from aider stderr, emits same `[runner] token_usage:` line as opencode — both backends now populate the Tokens tab

### Phase 3 — SGLang validation (done)
SGLang deployed as a separate Modal app (`agent-container-serve-sglang`); tool calling confirmed working with `hermes` parser.

- Add `sglang` profile to `serve.py` — separate app, same model/GPU as `test`
- Fix Modal env var injection — `SERVE_PROFILE` baked into secret at deploy time
- Fix SGLang image — switch to `nvidia/cuda:12.4.1-devel-ubuntu22.04` + `libnuma1` so JIT kernel compilation succeeds on A10G
- Fix warmup polling — `poll_interval` 5s → 30s to avoid flooding Modal with queued requests
- Fix tool-call parser — `qwen`/`qwen25` hangs on first tool-schema request; `hermes` resolves in 3s
- `make example BACKEND=opencode` against SGLang endpoint: tool call with 10 tools → ok (3.0s), PR opened

### Phase 4 — Model expansion + richer observability (done)
Broader model menu, live feedback during runs, serve endpoint validation.

- [#113](https://github.com/dvdthecoder/agent-container/issues/113) Expanded prod model registry: `qwen2.5-coder-7b`, `qwen3-30b`, `qwen3-coder`, `minimax-m2.5` alongside the default `qwen2.5-coder-32b`; `tool_call_parser` and `startup_timeout` are now per-model; each deploy gets a model-specific app name (`agent-container-serve-{slug}`) so multiple models run simultaneously
- Profile/model separation: `test` profile dissolved into `prod` with `qwen2.5-coder-32b` as the default `SERVE_MODEL`; only two profiles remain (`prod` vLLM, `experiment` SGLang)
- [#111](https://github.com/dvdthecoder/agent-container/issues/111) Heartbeat thread in `agent/runner.py` — prints `[runner] still running elapsed=Xs` every 30 s when the agent is silent; terminal never goes dark during a long RUNNING phase
- [#68](https://github.com/dvdthecoder/agent-container/issues/68) Serve endpoint integration tests — `tests/integration/test_serve_reachable.py` validates `GET /v1/models` (HTTP 200, model name present) and `POST /v1/chat/completions` (well-formed response); `.github/workflows/serve.yml` triggers manually after deploy
- 282 unit tests total (up from 271); new `serve` pytest marker

---

## Planned

### Phase 5 — Docs + quality tooling
Fill documentation gaps and add model comparison data.

| Issue | Task | Status |
|---|---|---|
| [#114](https://github.com/dvdthecoder/agent-container/issues/114) | Model × backend analysis: token/cost/quality comparison across self-hosted models | ✅ [2026-05-05](analysis/2026-05-05.md) baseline + [2026-05-08](analysis/2026-05-08.md) post-#150 + [2026-05-09](analysis/2026-05-09.md) post-think-strip |
| [#115](https://github.com/dvdthecoder/agent-container/issues/115) | Model profile guide — when to use 7B vs 32B vs MoE, GPU sizing rules | Planned |
| [#71](https://github.com/dvdthecoder/agent-container/issues/71) | `docs/lessons-learned.md` — hard problems, gotchas, team scaling guide | ✅ 25 entries |
| [#112](https://github.com/dvdthecoder/agent-container/issues/112) | Parse structured JSON events from opencode into SQLite (thinking token tracking) | Planned |
| [#153](https://github.com/dvdthecoder/agent-container/issues/153) | Re-run matrix post-#150 with both backends to confirm `<think>` strip + updated opencode/aider ratio | ✅ [2026-05-09](analysis/2026-05-09.md): Qwen3 −27%, ratio 8.4×, 32B 2/2 |

### Phase 6 — Frugal knowledge injection
Give agents the context they need to succeed without blowing the token budget.

Agents currently start cold — bare task string, no conventions, no file context. Each exploratory
tool turn costs ~26k tokens in opencode prompt overhead. The frugal answer is three layers of
selective context injection. See [Building Agent Context](context.md) for the full design.

| Issue | Task | Status |
|---|---|---|
| [#154](https://github.com/dvdthecoder/agent-container/issues/154) | Phase 1: `AGENTS.md` auto-injection — read from repo root, prepend to task prompt | Planned |
| [#154](https://github.com/dvdthecoder/agent-container/issues/154) | Phase 2: Structured YAML task spec — acceptance criteria, constraints, context files | Planned |
| [#154](https://github.com/dvdthecoder/agent-container/issues/154) | Phase 3: Diff scanner — secret detection, scope guardrails, OWASP checks | Planned |
| [#152](https://github.com/dvdthecoder/agent-container/issues/152) | Fix 32B reliability — verify diff non-empty before terminating after end_turn race | Planned |

### Phase 7 — Production hardening
Close the gap between the current implementation and a fully team-deployed system.

| Issue | Task |
|---|---|
| [#107](https://github.com/dvdthecoder/agent-container/issues/107) | Warm sandboxes via Modal snapshot API — eliminate cold-start clone+install latency |
| [#108](https://github.com/dvdthecoder/agent-container/issues/108) | Deeper verification loop — Sentry errors, metrics, visual screenshots for frontend |

### Phase 8 — Team and integrations
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
