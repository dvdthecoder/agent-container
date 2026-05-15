# Lessons Learned

Hard problems encountered while building agent-container, and the engineering decisions that
resolved them. Each entry is a real production bug that caused silent failures or wasted GPU
time before it was fixed.

---

## Pages

### [Deployment & Modal](lessons/deployment-modal.md)
Getting the stack running on Modal — vLLM cold start, sandbox lifecycle, Modal image cache,
SERVE_MODEL baking, vLLM version pinning, Qwen3 model IDs, GPU sizing for MoE models, SGLang.
9 lessons — #1, 2, 3, 4, 13, 15, 16, 17, 18

### [aider Backend](lessons/aider.md)
aider-specific configuration and output parsing — API URL mangling, repo map, pycache in
diffs, token count stdout vs stderr.
4 lessons — #5, 6, 7, 8

### [opencode & Proxy](lessons/opencode-proxy.md)
Running opencode against vLLM via the in-process proxy — Responses API translation, SSE
event sequence, `end_turn` race, `tool_choice` lifecycle, `task_complete` explicit signal,
edit vs write for function bodies, stopping a looping model.
10 lessons — #9, 10, 11, 19, 21, 28, 29, 31, 32, 33

### [Token Cost & Efficiency](lessons/token-cost.md)
Measuring and reducing token spend — aider abbreviated counts, Qwen3 thinking token
inflation, opencode cost model, aider/opencode structural gap, frugal injection principle.
6 lessons — #14, 20, 22, 23, 24, 25

### [Pipeline Reliability](lessons/pipeline-reliability.md)
Correctness gates between agent execution and recorded results — silent PR failures, diff
scanning (secrets, scope, OWASP), test gating, cross-tier test pollution.
4 lessons — #12, 26, 27, 30
