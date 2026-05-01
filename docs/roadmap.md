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
- `make example BACKEND=opencode` produces a real diff and opens a PR

---

## In progress

### Immediate
| Issue | Task |
|---|---|
| [#110](https://github.com/dvdthecoder/agent-container/issues/110) | Debug sandbox termination — containers still visible after run exits |
| — | Test dashboard end-to-end — spin up, run a real task, verify phase stream |
| [#106](https://github.com/dvdthecoder/agent-container/issues/106) | Phase 3: SGLang validation — deploy sglang profile, run opencode smoke test |

---

## Planned

### Phase 3 — Model expansion + observability
Broaden the model menu and make runs observable beyond the final PR.

| Issue | Task |
|---|---|
| [#113](https://github.com/dvdthecoder/agent-container/issues/113) | Add Qwen3 and Gemma 4 model profiles to `serve.py` |
| [#114](https://github.com/dvdthecoder/agent-container/issues/114) | Docs: cost and quality comparison — self-hosted LLMs vs Claude API |
| [#115](https://github.com/dvdthecoder/agent-container/issues/115) | Docs: step-by-step guide for adding a new model profile |
| [#112](https://github.com/dvdthecoder/agent-container/issues/112) | Install opencode-monitor in sandbox — structured per-tool-call events in logs |
| [#111](https://github.com/dvdthecoder/agent-container/issues/111) | Stream real-time agent progress in CLI during RUNNING phase |

### Phase 4 — Production hardening
Close the gap between the current implementation and a fully team-deployed system.

| Issue | Task |
|---|---|
| [#107](https://github.com/dvdthecoder/agent-container/issues/107) | Warm sandboxes via Modal snapshot API — eliminate cold-start clone+install latency |
| [#108](https://github.com/dvdthecoder/agent-container/issues/108) | Deeper verification loop — Sentry errors, metrics, visual screenshots for frontend |

### Phase 5 — Team and integrations
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
