# Token Cost & Efficiency

Lessons about measuring, understanding, and reducing token consumption across backends and models.

---

## 14. aider abbreviates token counts (`2.7k` not `2,841`)

**Problem:** The token regex expected `[\d,]+` (integers with commas). Current aider prints
abbreviated counts: `2.7k sent, 109 received.` The regex matched nothing → `prompt=0`.

**Fix:** Updated regex to `[\d,.]+[kKmM]?` and added `_parse_tok()` that handles plain ints,
comma-separated, `k`/`K`, and `m`/`M` suffixes.

---

## 20. Qwen3 thinking tokens inflate prompt context on subsequent turns

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

## 22. opencode prompt cost is model-size-independent — GPU choice affects speed, not tokens

**Finding (2026-05-09):** `qwen2.5-coder-7b` and `qwen2.5-coder-32b` both averaged ~27,700
total tokens per run on opencode ($0.0277 each at $1.00/1M). The prompt is dominated by tool
schemas and session history — both constant regardless of model size. Upgrading from 7B to
32B does not increase token cost; it increases GPU cost (A10G → A100-80GB, roughly 3–4×/hr)
and reduces wall-clock time for complex edits.

**Rule:** Choose model size based on task complexity and latency, not token budget. For tasks
where 32B produces fewer turns, it can be cheaper end-to-end despite the higher GPU rate.

---

## 23. aider is 8.4× cheaper per prompt than opencode — but the comparison is asymmetric

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

## 24. Qwen3 thinking costs +26% even after stripping — vLLM counts them regardless

**Finding (2026-05-09):** After `<think>` stripping, Qwen3-30B still cost $0.0349 vs $0.0277
for Coder models (+26%). vLLM counts thinking tokens in completion regardless of proxy
stripping — the model generates them and the endpoint bills for them (~1,056 completion tokens
vs ~117–274 for Coder models).

**Rule:** Reserve Qwen3 for tasks where reasoning depth demonstrably improves output quality
(algorithm design, subtle correctness bugs). For mechanical tasks Qwen2.5-Coder is cheaper
and faster.

---

## 25. The frugal injection principle — conventions only pay off when the agent would otherwise explore

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
