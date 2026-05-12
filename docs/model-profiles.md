# Model Profile Guide

When to use each model, which GPU to choose, and what each tier costs.

See [Model Setup](models.md) for deployment commands and env var configuration.

---

## Quick selection guide

| Situation | Recommended model | Reason |
|---|---|---|
| Getting started / iterating fast | `qwen2.5-coder-32b` (default) | Reliable tool use, proven on fixture suite, low cold-start |
| Simple single-file fixes, tight budget | `qwen2.5-coder-7b` + **aider** | 8× cheaper with aider backend; fine for well-scoped tasks |
| Complex multi-file or exploratory tasks | `qwen2.5-coder-32b` + opencode | Richer agentic loop; 32B handles ambiguity better than 7B |
| Algorithmic depth / subtle correctness bugs | `qwen3-30b` | Reasoning model; thinking tokens add ~26% cost but improve quality on hard problems |
| Production output quality | `qwen3-coder` (80B) | Largest coding-specific model; 2× A100 80GB |
| Context-heavy tasks (1M token window) | `minimax-m2.5` | 8× A100; use only when context length is the constraint |

---

## Per-model profiles

### `qwen2.5-coder-7b` — Budget tier

| | |
|---|---|
| GPU | A10G (24 GB) |
| Cold start | ~1 min |
| opencode run cost | ~$0.028 (tokens dominated by framework overhead) |
| aider run cost | ~$0.004 (8.4× cheaper — aider sends only task + changed files) |
| Context window | 32k tokens |

**Use when:** task is well-scoped (explicit file, explicit fix), using **aider** backend.
With aider, the 7B model costs 8.4× less than opencode on the same task because aider
sends ~3,300 prompt tokens vs opencode's ~27,600.

**Don't use when:** task requires exploring the repo, resolving ambiguity, or multi-step
tool chains — 7B struggles with these even with opencode.

---

### `qwen2.5-coder-32b` — Default (recommended starting point)

| | |
|---|---|
| GPU | A100 80GB |
| Cold start | ~1–2 min |
| opencode run cost | ~$0.028 |
| aider run cost | ~$0.003 |
| Context window | 32k tokens |

**The default for a reason:** Proven on fixture suite (2/2 success after #152 diff-retry fix),
stable tool use, handles multi-turn tool chains reliably. Upgrading from 7B to 32B does not
increase token cost — prompt tokens are dominated by framework overhead (tool schemas + session
history), which is constant regardless of model size. The extra cost is purely GPU time
(A10G → A100 80GB, roughly 3–4×/hr) in exchange for better output quality and fewer retries.

**Use when:** default choice for most tasks.

---

### `qwen3-30b` — Reasoning tier

| | |
|---|---|
| GPU | A100 80GB |
| Cold start | ~2–3 min |
| opencode run cost | ~$0.035 (+26% vs 32B Coder) |
| Context window | 32k tokens |

**Qwen3 is a hybrid think/non-think model.** It emits `<think>` blocks before each response.
The proxy strips `<think>` content from assistant messages before they re-enter session history
(−27% prompt tokens vs unstripped baseline), but vLLM still **counts thinking tokens in
completion** — you pay for them regardless.

**Measured overhead (2026-05-09):** Qwen3-30B averaged 1,056 completion tokens vs 117 for
Coder models on the same task. That's the irreducible cost of reasoning.

**Use when:** task involves algorithm design, subtle correctness bugs, or any case where
reasoning depth demonstrably improves output. For mechanical tasks (rename a variable,
fix a typo, add a test) use Coder models — they're cheaper and faster.

**GPU sizing note:** Qwen3-30B is a **MoE model** (3B active, 30B total). vLLM loads **all**
expert weights regardless of sparsity. At BF16, 30B parameters = ~60 GB → requires A100 80GB.
An A100-40GB OOMs during CUDA graph capture. See [lesson #18](lessons-learned.md#18).

---

### `qwen3-coder` — Production quality

| | |
|---|---|
| GPU | 2× A100 80GB |
| Cold start | ~3–5 min |
| Context window | 128k tokens |

80B parameter coding-specific model. Use when output quality on complex code generation
is the primary concern and cost is secondary.

The 128k context window enables whole-repository context on medium-sized codebases.

---

### `minimax-m2.5` — Maximum context

| | |
|---|---|
| GPU | 8× A100 80GB |
| Cold start | ~8–12 min |
| Context window | 1M tokens |

Reserve for tasks where context length is the hard constraint — e.g., whole-codebase
analysis, cross-file refactors in large repos, or multi-document synthesis. The 8× GPU
cost is substantial; do not use for routine tasks.

---

## GPU sizing rules

### Dense models — size by parameter count at BF16

| Model size | Memory required | Min GPU |
|---|---|---|
| 7B | ~14 GB | A10G (24 GB) ✓ |
| 32B | ~64 GB | A100 80GB ✓ |
| 32B | ~64 GB | A100 40GB ✗ (39.49 GiB available) |
| 80B | ~160 GB | 2× A100 80GB ✓ |

### MoE models — size by **total** parameter count, not active

vLLM loads all expert weights regardless of sparsity. Active-parameter count
is irrelevant for GPU selection.

| Model | Active params | Total params | Memory required | GPU |
|---|---|---|---|---|
| Qwen3-30B-A3B | 3B | 30B | ~60 GB | A100 80GB ✓ |
| MiniMax M2.5 | ~45B | ~456B | >640 GB | 8× A100 80GB |

**Rule:** When in doubt, use total parameter count × 2 bytes (BF16) and add 10% overhead for
KV cache and CUDA graphs.

---

## Backend × model cost matrix

Measured on the fixture task (single-file bug fix, 2026-05-09):

| Model | Backend | Prompt tok | Completion tok | Est. cost | Duration |
|---|---|---|---|---|---|
| qwen2.5-coder-7b | **aider** | 3,300 | 274 | **$0.0036** | 44s |
| qwen2.5-coder-7b | opencode | 27,573 | 126 | $0.0277 | 125s |
| qwen2.5-coder-32b | opencode | 27,583 | 117 | $0.0277 | 122s |
| qwen3-30b | opencode | 33,888 | 1,056 | $0.0349 | 127s |

**Key insight:** Upgrading from 7B to 32B within opencode costs the same in tokens —
both hit ~27,600 prompt tokens because the prompt is dominated by tool schemas + session
history, not model-specific content. The 32B runs faster on complex edits despite the
same token count, because it produces correct output in fewer turns.

**aider vs opencode at 7B:** 8.4× prompt token reduction because aider sends only the
task message and changed files. The trade-off: aider cannot browse arbitrary files or
react to shell output mid-session. Use aider for well-scoped single-file tasks; opencode
for exploratory or multi-step work. See [lesson #23](lessons-learned.md#23).

---

## Qwen model naming

The Qwen3 general-purpose model line does **not** use `-Instruct` suffixes on HuggingFace:

| Model | Correct HF ID | Wrong |
|---|---|---|
| Qwen3 8B | `Qwen/Qwen3-8B` | `Qwen/Qwen3-8B-Instruct` (404) |
| Qwen3 30B-A3B | `Qwen/Qwen3-30B-A3B` | `Qwen/Qwen3-30B-A3B-Instruct` (404) |
| Qwen3-Coder 80B | `Qwen/Qwen3-Coder-80B-Instruct` | (the Coder line keeps `-Instruct`) |
| Qwen2.5-Coder 32B | `Qwen/Qwen2.5-Coder-32B-Instruct` | |

Always verify HuggingFace repo IDs in the browser before adding a new model family.
See [lesson #17](lessons-learned.md#17).
