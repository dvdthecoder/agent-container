# Lessons Learned

Hard problems encountered while building agent-container, and the engineering decisions that
resolved them. Each entry is a real production bug that caused silent failures or wasted GPU
time before it was fixed.

---

## Infrastructure

### 1. vLLM cold start — 404s during boot

**Problem:** vLLM on an A10G takes ~75s for CUDA graph compilation. During this window all
routes return 404 including `/v1/chat/completions`. The agent would start, hit 404s, and
silently fail or loop until timeout.

**Fix:** WARMING phase polls `GET /v1/models` before the sandbox is even created. No container
is wasted on a cold server.

---

### 2. Sandbox not terminating — `terminate()` is fire-and-forget

**Problem:** `modal.Sandbox.terminate()` defaults to `wait=False`. The container kept running
in Modal's dashboard long after the CLI exited — visible as "still running" and billing GPU time.

**Fix:** Changed to `terminate(wait=True)`. `[sandbox] container terminated` in the logs means
the container is actually gone.

---

### 3. Hanging CLI after agent timeout

**Problem:** On timeout, `run_agent` called `sb.terminate()` then returned. `sandbox.py` then
called `collect_diff(sb)` — `sb.exec()` on a terminated container blocks indefinitely.

**Fix:** `run_agent` raises `TimeoutError` on timeout instead of returning. This skips
`collect_diff` entirely and returns a failure result immediately.

---

### 4. Runner script changes not picked up — Modal image cache

**Problem:** `add_local_file(..., copy=True)` bakes files into the Modal image layer at build
time. Code fixes to `aider_runner.py` and `opencode_runner.py` ran against stale containers
for 30+ minutes after merge.

**Fix:** Removed `copy=True` (reverted to default `copy=False`). Runner scripts are uploaded
fresh from local disk on every sandbox start. The expensive layers (pip, nodejs, opencode
install) remain cached.

---

## aider backend

### 5. aider's `--openai-api-base` flag mangles the URL

**Problem:** aider sets `OPENAI_API_BASE` from `--openai-api-base`. litellm reads
`OPENAI_API_BASE` and strips the `/v1` suffix, resulting in requests to `/chat/completions` — a
404 on every call.

**Fix:** Use `OPENAI_BASE_URL` (not `OPENAI_API_BASE`). The `/v1` suffix is guaranteed by
`SandboxConfig.env_for_backend("aider")` before the container starts.

---

### 6. Model asking clarifying questions instead of writing code

**Problem:** `--map-tokens 0` disabled the repo map. Without any file context the model replied
with questions ("where should I add this?") instead of editing files — empty diff, false failure.

**Fix:** Changed to `--map-tokens 1024`. Gives the model a concise file list and function
signatures without the full multi-minute scan.

---

### 7. `__pycache__` polluting PR diffs

**Problem:** The TESTING phase runs pytest which compiles `.pyc` files. These appeared in
`collect_diff` as the only changes — masking whether the agent wrote any real code.

**Fix:** After cloning, `git_ops.clone()` writes common build artifact patterns to
`.git/info/exclude` — local-only, never committed, keeps diffs clean.

---

### 8. aider token line appears on stdout, not stderr

**Problem:** Older aider printed `Tokens: X sent, Y received.` to stderr. Current versions
print it to stdout. `aider_runner.py` only scanned `is_stderr=True`, so every run logged
`prompt=0 completion=0 total=0`.

**Fix:** Scan both stdout and stderr for the token pattern.

---

## opencode backend

### 9. opencode calls Responses API — vLLM doesn't implement it

**Problem:** opencode v1.14+ calls `POST /v1/responses` (OpenAI Responses API). No self-hosted
server implements this. Every request returned 404.

**Fix:** `opencode_runner.py` starts a thin in-process HTTP proxy on `localhost:8080` that
translates Responses API to Chat Completions. Three behaviours were required beyond basic translation:

1. **Full SSE event sequence.** The proxy emits `response.output_item.added`,
   `response.function_call_arguments.done`, and `response.output_item.done` before
   `response.completed` for every tool call. Without these, opencode's loop does not detect
   the tool call and the session ends with no changes.

2. **`parallel_tool_calls: false`.** Without this the model calls `read` and `edit` in the
   same response, generating `oldString` from prior knowledge. The edit fails silently.

3. **Adaptive `tool_choice`.** `tool_choice: "required"` on the first turn forces a tool call.
   After an `edit`/`write` appears in history, the proxy switches to `"auto"` so the model can
   return a final text response and end the session.

---

### 10. Proxy crashes on usage-only SSE chunks

**Problem:** vLLM sends a final chunk with `"choices": []` and `"usage": {...}` when
`stream_options.include_usage=true`. `chunk.get("choices", [{}])[0]` returns `[][0]` because
`get()` returns the real empty list, not the default — `IndexError` → 502 back to opencode →
empty diff → run marked failed.

**Fix:** `(chunk.get("choices") or [{}])[0]` — empty list is falsy, so the default fires.

---

### 11. Token usage only emitted on success path

**Problem:** `[runner] token_usage:` was only printed when `stop_reason == "session_completed"`.
opencode always exits via the `end_turn` grace-period path — that path had no emit, so every
opencode run had `total_tokens=NULL` in SQLite.

**Fix:** Extracted `_emit_token_usage()` helper called on all three exit paths: unexpected
stop, `end_turn` + grace period, and deadline-loop timeout.

---

## PR creation

### 12. PR failures were completely silent

**Problem:** Four silent failure modes in `push_and_pr`:
1. Unsupported host → `return (br, None)` with no log
2. Missing token → `return (br, None)` with no log
3. `_git()` read stderr after `wait()` — Modal drains streams on `wait()`, so error messages
   were always empty strings
4. `curl -f` suppressed the response body on API errors; `data.get(url_field)` returned
   `None` silently instead of raising

**Fix:** All four paths now log or raise with the actual failure reason. Missing token prints
which env var to set. API errors surface the GitHub error message.

---

## SGLang

### 13. SGLang v0.4.7 tool-calling crashes

**Problem:** SGLang v0.4.7 had multiple blocking bugs: `--enable-auto-tool-choice` did not
exist, `--tool-call-parser qwen25` crashed the server on the first request with tool schemas,
and streaming with tools hung indefinitely. The original proxy had 389 lines of model-specific
workarounds (Qwen-native text injection, text-level `<tool_call>` parsing).

**Phase 1 fix:** Switched primary inference to vLLM. All SGLang workarounds removed — proxy
became a clean format adapter (170 lines added, 389 removed).

**Phase 3 re-validation:** SGLang re-tested with Qwen2.5-Coder 32B on A100 80GB:
- `qwen` and `qwen25` parsers still hang on first request with tool schemas
- `hermes` parser works — first tool call returned in 3s, full run in 29s
- Requires `nvidia/cuda:12.4.1-devel-ubuntu22.04` base image and `libnuma1` (JIT kernel
  compilation at model-load time fails in debian-slim)

**Conclusion:** SGLang is viable with `hermes`. vLLM remains the default (works out-of-the-box).
Both run simultaneously as separate Modal apps.

---

## Token tracking

### 14. aider abbreviates token counts (`2.7k` not `2,841`)

**Problem:** The token regex expected `[\d,]+` (integers with commas). Current aider prints
abbreviated counts: `2.7k sent, 109 received.` The regex matched nothing → `prompt=0`.

**Fix:** Updated regex to `[\d,.]+[kKmM]?` and added `_parse_tok()` that handles plain ints,
comma-separated, `k`/`K`, and `m`/`M` suffixes.

---

## Modal deployment

### 15. SERVE_MODEL not baked into container — wrong model loads every time

**Problem:** `SERVE_MODEL` is read at module level in `modal/serve.py`. `SERVE_PROFILE` was
baked into the Modal secret but `SERVE_MODEL` was not. Every deployment — including isolated
per-model apps — always loaded `qwen2.5-coder-32b` regardless of which model was passed at
deploy time.

**Fix:** Bake `SERVE_MODEL` into the Modal secret alongside `SERVE_PROFILE`.

**Rule:** Any env var read at module level that controls runtime behaviour must be baked into
the container secret — not just passed at deploy time.

---

### 16. vLLM pin cascade — don't pin reactively

**Problem:** Two crashes surfaced as version conflicts but were caused by the wrong model
loading (the #15 bug). When the container loaded `qwen2.5-coder-32b` on an A10G instead of
`qwen3-8b`, the tokenizer init failed. First instinct: pin `vllm==0.8.5` → then
`AttributeError: Qwen2Tokenizer has no attribute all_special_tokens_extended` →
pin `transformers==4.46.3` → conflicts because vLLM 0.8.5 requires `transformers>=4.51.1`.

**Fix:** Remove all version pins and fix the root cause (#15). With the correct model loading,
the latest `vllm` + `transformers` work without conflicts.

**Rule:** Don't pin vLLM reactively to silence a crash. Check which model is actually loading
first (`grep "Starting to load model"` in container logs). An OOM or tokenizer error on the
wrong model is a config bug, not a version bug.

---

### 17. Qwen3 general models don't use the -Instruct suffix

**Problem:** `Qwen/Qwen3-8B-Instruct` and `Qwen/Qwen3-30B-A3B-Instruct` return 404 on
HuggingFace. Qwen3 general-purpose models are released as hybrid think/non-think checkpoints
without a separate instruct variant — the base repo is the instruction-tuned model.

**Fix:** Drop the `-Instruct` suffix: `Qwen/Qwen3-8B`, `Qwen/Qwen3-30B-A3B`.

**Rule:** The Qwen3-Coder line (`Qwen/Qwen3-Coder-80B-Instruct`) does keep `-Instruct`.
Verify HF repo IDs in the browser before adding a new model family.

---

### 18. Qwen3-30B-A3B needs A100-80GB, not A100-40GB

**Problem:** Qwen3-30B-A3B is a MoE model. vLLM loads all expert weights regardless of
sparsity. At BF16, 30B parameters = ~60 GB — does not fit on an A100-40GB (39.49 GiB).
Container OOMed during CUDA graph capture.

**Fix:** Changed GPU to `A100-80GB`.

**Rule:** Size the GPU on **total** parameter count for MoE models, not active parameter count.

---

### 19. opencode `end_turn` race — file write not flushed before diff collection

**Problem:** opencode occasionally returns `stopReason='end_turn'` before the file write
from the preceding `edit` tool call is flushed to disk. Runner terminates the sandbox,
`collect_diff` finds an empty diff — even though the proxy confirmed the `edit` was received.
Observed as ~33% flake rate on `qwen2.5-coder-32b` / opencode.

**Root cause:** `end_turn` is a session-level signal that the model finished responding —
it does not guarantee all tool-call side effects are durably committed.

**Fix (2026-05-09, #152):** After the runner exits 0 with an empty diff, `collect_diff` is
retried up to 3× with 5s delays before raising `PhaseError`. A briefly-delayed write is
no longer mis-classified as a no-op failure.

---

### 20. Qwen3 thinking tokens inflate prompt context on subsequent turns

**Problem:** Qwen3 emits `<think>` blocks (~1,300–2,400 tokens per response) that accumulate
in the conversation history and are resent as context on every turn. For a 3-turn run:

- Baseline (Coder models): ~26k prompt tokens
- Qwen3 with thinking: ~46k prompt tokens (+77%)

**Fix:** `_strip_think()` in the proxy strips `<think>` blocks before the assistant message
re-enters opencode's session history. Measured result (2026-05-09): avg prompt tokens dropped
from 46,149 → 33,888 (−27%). Completion tokens remain higher (~1,056 vs ~117 for Coder
models) because vLLM counts thinking in completion regardless — stripping only prevents
compounding across turns.

---

### 21. OPENCODE_MODEL must be updated together with OPENAI_BASE_URL

**Problem:** `wait_for_serve --update-env` updates `OPENAI_BASE_URL` in `.env` but not
`OPENCODE_MODEL`. Sending requests to the qwen3-30b endpoint asking for `qwen2.5-coder-32b`
returns HTTP 404 on every tool turn.

**Fix:** Always set `OPENCODE_MODEL` explicitly alongside the endpoint — either via `sed`
or as a shell env var prefix. `run_matrix.py` handles this correctly; the bug only surfaces
when running `token_analysis.py` directly after `wait_for_serve`.

**Rule:** `OPENAI_BASE_URL` and `OPENCODE_MODEL` are a pair. Treat them as a unit.

---

## Cost optimisation

### 22. opencode prompt cost is model-size-independent — GPU choice affects speed, not tokens

**Finding (2026-05-09):** `qwen2.5-coder-7b` and `qwen2.5-coder-32b` both averaged ~27,700
total tokens per run on opencode ($0.0277 each at $1.00/1M). The prompt is dominated by tool
schemas and session history — both constant regardless of model size. Upgrading from 7B to
32B does not increase token cost; it increases GPU cost (A10G → A100-80GB, roughly 3–4×/hr)
and reduces wall-clock time for complex edits.

**Rule:** Choose model size based on task complexity and latency, not token budget. For tasks
where 32B produces fewer turns, it can be cheaper end-to-end despite the higher GPU rate.

---

### 23. aider is 8.4× cheaper per prompt than opencode — but the comparison is asymmetric

**Finding (2026-05-09):** aider averaged 3,300 prompt tokens vs opencode's 27,573 on the same
model/task/endpoint (8.4× ratio). Gap narrowed from 11.9× (baseline) via description
stripping (−19%) and post-edit tool filtering.

**Why the gap exists:** opencode resends all 10 tool schemas (~500 tokens each) on every turn
plus accumulated conversation history. aider sends only the task message and changed files.

**Asymmetry:** aider's smaller prompt comes at the cost of a less capable tool loop — it
cannot browse arbitrary files or react to shell output mid-session. opencode's overhead buys
a richer agentic loop. Use aider for well-scoped single-file tasks; opencode for exploratory
or multi-step work.

**Irreducible floor:** the remaining ~8× gap is structural Responses API overhead. Further
reduction requires prompt prefix caching at the vLLM level (not yet enabled).

---

### 24. Qwen3 thinking tokens add ~26% cost even after stripping — vLLM counts them regardless

**Finding (2026-05-09):** After `<think>` stripping, Qwen3-30B still cost $0.0349 vs $0.0277
for Coder models (+26%). vLLM counts thinking tokens in completion regardless of proxy
stripping — the model generates them and the endpoint bills for them (~1,056 completion tokens
vs ~117–274 for Coder models).

**Rule:** Reserve Qwen3 for tasks where reasoning depth demonstrably improves output quality
(algorithm design, subtle correctness bugs). For mechanical tasks Qwen2.5-Coder is cheaper
and faster.

---

### 25. The frugal injection principle — conventions only pay off when the agent would otherwise explore

**Principle:** Every token added to the task prompt is a cost. Context injection is only
worthwhile if it eliminates more tokens than it introduces — by replacing exploratory tool
calls the agent would otherwise make.

**Measured (2026-05-12, #155):** Injecting `AgentTaskSpec.conventions` (~300 tokens) against
a 3-file repo with an explicit task added **+334 prompt tokens** and saved **zero turns**.
Tool trace was identical with and without conventions: `read → edit`. The agent already knew
exactly what to do from the task string — conventions were inert.

**When conventions pay off — scales with exploratory overhead:**

| Scenario | Without conventions | Savings |
|---|---|---|
| 3-file repo, explicit task | `read` → `edit` | nothing |
| 50-file repo, vague task | `glob` → `read` × 3–5 → `edit` | 3–5 turns (~15–25k tokens) |
| Any task, non-obvious test command | test-discovery turn | ~3k tokens |

**Highest-value injections (always worth it):**
- The test command (`pytest test_mathlib.py -q`) — eliminates a discovery turn every time
- Which file to touch when the repo has 50+ files — replaces 2–4 glob/read calls
- Acceptance criteria — helps the model recognize "done" and produce a clean `end_turn`

**Not worth injecting:**
- Repo structure when the task names the file explicitly
- Full file content when the agent will read it in turn 1 anyway
- Conventions that don't apply to the specific task

**Rule:** Ask "would the agent call glob or read N files to discover this?" If yes, inject it.
If the task string already encodes the answer, skip it.

---

## Security

### 26. Diff scanning — catch secrets and scope drift before push

**Problem:** Agents can accidentally include hardcoded secrets (copied from env vars or examples),
modify files outside the intended scope, or introduce insecure patterns (eval, shell=True,
pickle.loads) without any gate between `git diff` and `git push`.

**Fix:** SCANNING phase runs `scan_diff()` immediately after `collect_diff` (and after the
empty-diff retry). Three rule categories:

1. **Secrets (error, blocking)** — AWS access keys, GitHub PATs, OpenAI keys, Slack tokens,
   generic hardcoded credential assignments.  Run blocked; PhaseError raised.
2. **Scope violations (warning, non-blocking)** — files modified outside `context_files` declared
   in the YAML task spec.  Logged to stderr; run continues.
3. **OWASP patterns (warning, non-blocking)** — eval(), shell=True, os.system(), pickle.loads(),
   unsafe yaml.load().  High-signal but too many legitimate uses to block.

**Rule:** Blocking on secrets is safe (precision is high).  Blocking on OWASP is not — test
harnesses legitimately use eval and subprocess.  Warnings give the operator visibility without
false failures.
