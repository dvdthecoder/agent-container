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
time. Modal caches that layer aggressively. Code fixes to `aider_runner.py` and
`opencode_runner.py` ran against stale containers for 30+ minutes after merge.

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
signatures (a few seconds on small repos) without the full multi-minute scan.

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
opencode always exits via the `end_turn` grace-period path (`session/prompt` returns
`end_turn`, runner waits 90s for `session_completed`, terminates). That path had no emit,
so every opencode run had `total_tokens=NULL` in SQLite.

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

**Problem:** `modal/serve.py` resolves `SERVE_MODEL` via `os.environ.get("SERVE_MODEL", _PROD_DEFAULT)` at module level. The container re-imports the module on every function invocation. `SERVE_PROFILE` was baked into the Modal secret but `SERVE_MODEL` was not. Result: every deployment — including isolated per-model apps — always loaded `qwen2.5-coder-32b` regardless of which model was passed at deploy time. `qwen3-8b` targeted an A10G (correct) but tried to load the 32B model (22GB OOM).

**Fix:** Added `"SERVE_MODEL": os.environ.get("SERVE_MODEL", _PROD_DEFAULT)` to the Modal secret alongside `SERVE_PROFILE`. Rule: any env var read at module level that controls runtime behaviour must be baked into the container secret.

---

### 16. vLLM + transformers pin cascade

**Problem:** Two separate crashes, same root cause (wrong model loading in the container):

1. `Engine core initialization failed. Failed core proc(s): {}` — observed with unpinned vLLM
   (0.9.x). First instinct: pin to `vllm==0.8.5`.

2. `AttributeError: Qwen2Tokenizer has no attribute all_special_tokens_extended` — observed after
   pinning to `vllm==0.8.5`. vLLM 0.8.5 calls this in `get_cached_tokenizer()`. transformers
   4.51.1+ (required by vLLM 0.8.5) removed the attribute from `Qwen2TokenizerFast`.
   Pin attempt `transformers==4.46.3` conflicts: vLLM 0.8.5 requires `transformers>=4.51.1`.

**Root cause:** Both crashes were triggered by the wrong model being loaded in the container
(the SERVE_MODEL-not-baked bug, #15). When the container loaded `qwen2.5-coder-32b` on an
A10G (22 GB) instead of the intended `qwen3-8b`, the tokenizer init failed with the attribute
error, which surfaced as an engine core crash.

**Fix:** Remove all version pins — use `pip_install("vllm", "huggingface_hub[hf_transfer]")`.
With SERVE_MODEL correctly baked into the container secret (#15 fix), each container loads the
intended model and the tokenizer init succeeds against the latest transformers.

**Rule:** Don't pin vLLM reactively to silence a crash. Diagnose which model is actually loading
first (`grep "Starting to load model"` in container logs). An OOM or tokenizer error on the
wrong model is a config bug, not a vLLM version bug.

---

### 17. Qwen3 general models don't use the -Instruct suffix

**Problem:** `Qwen/Qwen3-8B-Instruct` and `Qwen/Qwen3-30B-A3B-Instruct` return 404 on
HuggingFace. Qwen3 general-purpose models (8B, 30B-A3B, 32B, 235B) are released as hybrid
think/non-think checkpoints without a separate instruct variant — the base repo is the
instruction-tuned model.

**Fix:** Drop the `-Instruct` suffix: `Qwen/Qwen3-8B`, `Qwen/Qwen3-30B-A3B`.

**Rule:** The Qwen3-Coder line (`Qwen/Qwen3-Coder-80B-Instruct`) does keep `-Instruct`.
Verify HF repo IDs in the browser before adding a new model family.

---

### 18. Qwen3-30B-A3B needs A100-80GB, not A100-40GB

**Problem:** Qwen3-30B-A3B is a MoE model with 30B total parameters and ~3B active per
forward pass. vLLM loads all expert weights regardless of sparsity. At BF16, 30B parameters
= ~60 GB — does not fit on an A100-40GB (39.49 GiB). The container OOMed during CUDA graph
capture, which surfaced as the V1 engine-core crash.

**Fix:** Changed GPU to `A100-80GB`. The 80 GB card has sufficient headroom for weights +
KV cache.

**Rule:** For any MoE model, size the GPU based on **total** parameter count, not active
parameter count. vLLM's `gpu_memory_utilization` does not help if the weights alone exceed
VRAM.

---

### 19. opencode `end_turn` grace period — file write race condition

**Problem:** opencode occasionally returns `stopReason='end_turn'` from `session/prompt`
before the file write from the preceding `edit` tool call is flushed to disk. The runner
waits 90s for `session_completed`, terminates the sandbox when it doesn't arrive, and
`collect_diff` finds an empty diff — even though the proxy confirmed the `edit` tool call
was received and acknowledged by opencode. Observed as a ~33% flake rate on
`qwen2.5-coder-32b` / opencode (1 failure in 3 runs, run-20260505-072253-d103de).

**Symptom:** Proxy log shows `edit args={...}` and the model returned a clean text
response; `session/prompt` returns `end_turn`; 90s grace period elapses; diff is empty.

**Root cause:** `end_turn` is a session-level signal from opencode that the model finished
responding — it does not guarantee all tool-call side effects (file writes) are durably
committed before the grace period expires.

**Mitigation (not yet fixed):** After the grace period, verify the diff is non-empty before
terminating. If empty, extend the wait and re-check rather than treating it as a failure.
Tracked in #112 (structured events would make tool-call completion observable).

---

### 20. Qwen3 thinking tokens inflate prompt context on subsequent turns

**Problem:** Qwen3 is a hybrid think/no-think model that emits `<think>` blocks before its
final answer. These thinking outputs (~1,300–2,400 tokens per response) become part of the
conversation history and are resent as context on every subsequent turn. For a 3-turn run,
the accumulated thinking adds ~14k tokens on top of the normal framework overhead:

- Baseline framework overhead (Coder models): ~26k prompt tokens
- Qwen3 with thinking: ~46k prompt tokens (+44%)

**Implication:** Qwen3's higher prompt cost is not from better outputs — completion tokens
for the actual code change are similar or lower. It's pure thinking verbosity in the context
window. Qwen3 costs ~1.8× per run compared to Coder models on opencode, making it a poor
cost/quality trade-off for straightforward coding tasks.

**Mitigation options:**
1. Strip `<think>` blocks from assistant messages before returning them to opencode (proxy-level).
2. Use `OPENCODE_TOOL_CHOICE=auto` with a model that has native thinking-budget controls.
3. Prefer Qwen2.5-Coder models for tasks where reasoning depth is not needed.

**Fix implemented:** `_strip_think()` in the proxy strips closed and unclosed `<think>` blocks
before the assistant message re-enters opencode's session history. Measured result (2026-05-09):
Qwen3 avg prompt tokens dropped from 46,149 → 33,888 (−27%). Completion tokens are still higher
(~1,056 vs ~117 for Coder models) because vLLM counts thinking tokens in completion regardless —
stripping only prevents them from compounding across turns.

---

## Cost optimisation

### 22. opencode prompt cost is model-size-independent — GPU choice affects speed, not tokens

**Finding (2026-05-09 matrix):** `qwen2.5-coder-7b` and `qwen2.5-coder-32b` both averaged
~27,700 total tokens per run on opencode ($0.0277 each at $1.00/1M). The prompt is dominated
by tool schemas and session history — both constant regardless of which model processes them.
Upgrading from 7B to 32B does not increase token cost; it increases GPU cost (A10G → A100-80GB,
roughly 3–4× more expensive per hour on Modal) and reduces wall-clock time for complex edits.

**Rule:** Choose model size based on task complexity and latency requirements, not token budget.
For simple bug fixes, 7B is as cheap as 32B on the token dimension. For tasks where 32B
produces fewer turns (better reasoning → shorter session), it can actually be *cheaper* end-to-end
despite the higher GPU rate.

---

### 23. aider is 8.4× cheaper per prompt than opencode — but the comparison is asymmetric

**Finding (2026-05-09):** aider averaged 3,300 prompt tokens vs opencode's 27,573 on the same
model, task, and endpoint (8.4× ratio). The gap has narrowed from 11.9× (baseline) via two
proxy optimisations: tool schema description stripping (−19%) and post-edit tool filtering.

**Why the gap exists:** opencode maintains a full Responses API session — tool schemas (~500
tokens each × 10 tools) are resent on every turn, plus accumulated conversation history.
aider sends only the task message and changed file contents. For a 3-turn run, aider's prompt
grows linearly with file size; opencode's grows with turns × schema size.

**Asymmetry:** the comparison is unfair for complex multi-file work. aider's smaller prompt
comes at the cost of less tool context — it cannot browse arbitrary files, run tests mid-session,
or react to shell output without explicit `--file` flags. opencode's overhead buys a richer
agentic loop. Use aider for well-scoped single-file tasks; opencode for exploratory or
multi-step work where the tool loop earns its keep.

**Irreducible floor:** the remaining ~8× gap is structural Responses API overhead. Further
reduction requires either a streaming session format that doesn't resend schemas, or caching
at the vLLM level (prompt prefix caching, not yet enabled).

---

### 24. Qwen3 thinking tokens add ~26% cost even after stripping — vLLM counts them regardless

**Finding (2026-05-09):** After `<think>` stripping, Qwen3-30B averaged 33,888 prompt tokens
(down from 46,149, −27%) but still cost $0.0349 vs $0.0277 for Coder models — a 26% premium.

**Why:** `<think>` stripping prevents thinking tokens from re-entering the context window on
subsequent turns (fixing the compounding problem). But vLLM counts thinking tokens in the
completion regardless of what the proxy strips — the model still *generates* them and the
endpoint bills for them. For this task Qwen3 emitted ~1,056 completion tokens vs ~117–274
for Coder models; the difference is almost entirely thinking.

**Rule:** `<think>` stripping is necessary to prevent exponential prompt growth across turns,
but it does not eliminate the per-turn thinking cost. For tasks where chain-of-thought reasoning
does not improve output quality (simple bug fixes, mechanical refactors), Qwen2.5-Coder models
are both cheaper and faster. Reserve Qwen3 for tasks where reasoning depth is demonstrably
needed (algorithm design, subtle correctness bugs).

---

### 25. The frugal injection principle — injected context must save more tokens than it adds

**Principle:** Every token added to the task prompt is a cost. Context injection is only
worthwhile if it eliminates more tokens than it introduces — typically by replacing exploratory
tool calls the agent would otherwise make.

**Examples:**
- `AGENTS.md` with repo structure + acceptance criteria: adds ~500–1,500 tokens but eliminates
  1–3 exploratory `glob`/`read` turns (~3,000–9,000 tokens saved). Net: positive.
- Injecting the full source file (5,000 tokens) when the agent would have read it anyway in
  turn 1 (~500 token `read` call + ~5,000 token response): net neutral at best, negative if
  the agent doesn't need the whole file.
- Injecting a test command string ("run `pytest test_mathlib.py -q`") eliminates a test
  discovery turn (~3,000 tokens). Net: strongly positive.

**Rule:** Measure before injecting. If the agent's first tool call is always `glob("**/*.py")`
followed by `read("mathlib.py")`, those two turns (~7,000 tokens) are the target. Provide
the file list and relevant file content upfront only if you are confident the agent will
always need them. Speculative injection that the agent ignores is pure waste.

---

## Knowledge injection

### 21. OPENCODE_MODEL must match the deployed endpoint — wait_for_serve --update-env only updates OPENAI_BASE_URL

**Problem:** After `python3 scripts/wait_for_serve.py --update-env`, `.env` has the new
`OPENAI_BASE_URL` pointing at the newly deployed model, but `OPENCODE_MODEL` still holds the
previous model name. The sandbox sends requests to the qwen3-30b endpoint asking for
`qwen2.5-coder-32b`, which returns HTTP 404 (`The model qwen2.5-coder-32b does not exist`).

**Symptom:** `[proxy] upstream 404` repeated on every tool turn; all runs fail immediately.

**Fix:** Always set `OPENCODE_MODEL` explicitly in the same command that sets the endpoint,
either via `sed` to update `.env` or by passing `OPENCODE_MODEL=<model>` as a shell env var
prefix. `run_matrix.py` handles this correctly by overriding both per-model — the bug only
surfaces when running `token_analysis.py` directly after `wait_for_serve`.

**Rule:** `OPENAI_BASE_URL` and `OPENCODE_MODEL` are a pair. Changing one without the other
will produce 404s. Treat them as a unit.
