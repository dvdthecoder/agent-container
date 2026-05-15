# Deployment & Modal

Lessons from getting the stack running on Modal — inference server setup, container lifecycle,
and model loading configuration.

---

## 1. vLLM cold start — 404s during boot

**Problem:** vLLM on an A10G takes ~75s for CUDA graph compilation. During this window all
routes return 404 including `/v1/chat/completions`. The agent would start, hit 404s, and
silently fail or loop until timeout.

**Fix:** WARMING phase polls `GET /v1/models` before the sandbox is even created. No container
is wasted on a cold server.

---

## 2. Sandbox not terminating — `terminate()` is fire-and-forget

**Problem:** `modal.Sandbox.terminate()` defaults to `wait=False`. The container kept running
in Modal's dashboard long after the CLI exited — visible as "still running" and billing GPU time.

**Fix:** Changed to `terminate(wait=True)`. `[sandbox] container terminated` in the logs means
the container is actually gone.

---

## 3. Hanging CLI after agent timeout

**Problem:** On timeout, `run_agent` called `sb.terminate()` then returned. `sandbox.py` then
called `collect_diff(sb)` — `sb.exec()` on a terminated container blocks indefinitely.

**Fix:** `run_agent` raises `TimeoutError` on timeout instead of returning. This skips
`collect_diff` entirely and returns a failure result immediately.

---

## 4. Runner script changes not picked up — Modal image cache

**Problem:** `add_local_file(..., copy=True)` bakes files into the Modal image layer at build
time. Code fixes to `aider_runner.py` and `opencode_runner.py` ran against stale containers
for 30+ minutes after merge.

**Fix:** Removed `copy=True` (reverted to default `copy=False`). Runner scripts are uploaded
fresh from local disk on every sandbox start. The expensive layers (pip, nodejs, opencode
install) remain cached.

---

## 13. SGLang v0.4.7 tool-calling crashes

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

## 15. SERVE_MODEL not baked into container — wrong model loads every time

**Problem:** `SERVE_MODEL` is read at module level in `modal/serve.py`. `SERVE_PROFILE` was
baked into the Modal secret but `SERVE_MODEL` was not. Every deployment — including isolated
per-model apps — always loaded `qwen2.5-coder-32b` regardless of which model was passed at
deploy time.

**Fix:** Bake `SERVE_MODEL` into the Modal secret alongside `SERVE_PROFILE`.

**Rule:** Any env var read at module level that controls runtime behaviour must be baked into
the container secret — not just passed at deploy time.

---

## 16. vLLM pin cascade — don't pin reactively

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

## 17. Qwen3 general models don't use the -Instruct suffix

**Problem:** `Qwen/Qwen3-8B-Instruct` and `Qwen/Qwen3-30B-A3B-Instruct` return 404 on
HuggingFace. Qwen3 general-purpose models are released as hybrid think/non-think checkpoints
without a separate instruct variant — the base repo is the instruction-tuned model.

**Fix:** Drop the `-Instruct` suffix: `Qwen/Qwen3-8B`, `Qwen/Qwen3-30B-A3B`.

**Rule:** The Qwen3-Coder line (`Qwen/Qwen3-Coder-80B-Instruct`) does keep `-Instruct`.
Verify HF repo IDs in the browser before adding a new model family.

---

## 18. Qwen3-30B-A3B needs A100-80GB, not A100-40GB

**Problem:** Qwen3-30B-A3B is a MoE model. vLLM loads all expert weights regardless of
sparsity. At BF16, 30B parameters = ~60 GB — does not fit on an A100-40GB (39.49 GiB).
Container OOMed during CUDA graph capture.

**Fix:** Changed GPU to `A100-80GB`.

**Rule:** Size the GPU on **total** parameter count for MoE models, not active parameter count.
