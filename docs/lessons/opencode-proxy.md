# opencode & Proxy

Lessons from running opencode against a self-hosted vLLM endpoint via the in-process proxy.
The proxy translates the OpenAI Responses API to Chat Completions and manages session lifecycle.

---

## 9. opencode calls Responses API — vLLM doesn't implement it

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

## 10. Proxy crashes on usage-only SSE chunks

**Problem:** vLLM sends a final chunk with `"choices": []` and `"usage": {...}` when
`stream_options.include_usage=true`. `chunk.get("choices", [{}])[0]` returns `[][0]` because
`get()` returns the real empty list, not the default — `IndexError` → 502 back to opencode →
empty diff → run marked failed.

**Fix:** `(chunk.get("choices") or [{}])[0]` — empty list is falsy, so the default fires.

---

## 11. Token usage only emitted on success path

**Problem:** `[runner] token_usage:` was only printed when `stop_reason == "session_completed"`.
opencode always exits via the `end_turn` grace-period path — that path had no emit, so every
opencode run had `total_tokens=NULL` in SQLite.

**Fix:** Extracted `_emit_token_usage()` helper called on all three exit paths: unexpected
stop, `end_turn` + grace period, and deadline-loop timeout.

---

## 19. opencode `end_turn` race — file write not flushed before diff collection

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

## 21. OPENCODE_MODEL must be updated together with OPENAI_BASE_URL

**Problem:** `wait_for_serve --update-env` updates `OPENAI_BASE_URL` in `.env` but not
`OPENCODE_MODEL`. Sending requests to the qwen3-30b endpoint asking for `qwen2.5-coder-32b`
returns HTTP 404 on every tool turn.

**Fix:** Always set `OPENCODE_MODEL` explicitly alongside the endpoint — either via `sed`
or as a shell env var prefix. `run_matrix.py` handles this correctly; the bug only surfaces
when running `token_analysis.py` directly after `wait_for_serve`.

**Rule:** `OPENAI_BASE_URL` and `OPENCODE_MODEL` are a pair. Treat them as a unit.

---

## 28. opencode multi-line edit indentation bug

**Problem:** opencode's `edit` tool does a literal substring find-and-replace.  When the model
writes a multi-line `newString`, it generates the code as standalone text with no leading
indentation.  The edit tool prepends the original line's indentation only to the first line of
the replacement — all subsequent lines after `\n` land at column 0.

Example: replacing `    raise NotImplementedError(...)` (4-space indent inside a function) with:
```
newString: "values.sort()\nmid = len(values) // 2\nif ..."
```
Result: `values.sort()` is correctly indented, `mid = ...` and `if ...` land at module level →
`SyntaxError: return outside function`.

Reproduced consistently across multiple runs.  aider avoids this by using whole-file
SEARCH/REPLACE blocks that include the full surrounding indented context.

**Fix:** Inject an explicit editing rule into the task conventions:
> "When replacing code inside an indented block, every line of `newString` must include the
> correct leading spaces as they appear in the file — not as standalone code."

**Rule:** opencode's edit primitive requires indentation-aware prompting.  aider's
whole-file format is self-healing.  On tasks with multi-line replacements inside indented
blocks, opencode needs explicit instruction or it will produce SyntaxErrors.

---

## 29. opencode stops early on multi-step tasks — tool_choice:auto escape

**Problem:** The proxy switches `tool_choice` from `required` to `auto` after the first
`edit`/`write` call (intended to prevent infinite bash loops).  With `auto`, the model can
respond with text only and zero tool calls, declaring itself done.

On Tier 3 (rename 4 functions across 2 files): opencode renamed one function
(`calc_area_rect` → `area_rectangle`) then produced a 150-char text response and stopped.
The other three renames and `shapes.py` were never touched.

aider doesn't have this problem because it runs tests after every edit — test failures force
continued iteration.  opencode relies on the model planning all steps upfront; when `auto`
offers an exit, it takes it.

**Fix:** Inject an explicit planning rule into conventions:
> "Before making any edit, enumerate ALL changes required.  Do not produce a text response
> until every planned edit has been applied and tests pass."

**Rule:** `tool_choice:auto` is a necessary escape valve but it lets under-planning go
undetected.  Compensate with explicit "plan first, complete all steps" instructions.
opencode on multi-step tasks without these instructions will under-deliver reliably.

---

## 31. opencode edit tool fails on function bodies — use write instead

**Problem:** opencode's `edit` tool asks the model to provide `oldString` (the exact text to
replace) and `newString` (the replacement).  For implementing a stub function the `oldString`
is typically just the single `raise NotImplementedError(...)` line — with no surrounding
function signature context.  The model must then generate the entire multi-line function body
as the `newString` value inside a JSON string, where:

- Every `\n` must be encoded as a literal backslash-n inside the JSON string.
- Every line of indentation must be produced as explicit spaces (4 per level) with no
  surrounding context to anchor the model's whitespace.
- Any truncation or omission (e.g. forgetting the final `return sorted_vals[mid]`) is silent —
  the proxy accepts a truncated newString and the file ends up with an incomplete body.

Observed failure mode across T2 runs (89k token run):

1. Model calls `edit(oldString="raise NotImplementedError(...)", newString="    sorted_vals…\n  ")` — correct start, truncated end (missing `return sorted_vals[mid]`).
2. Tests fail (function returns `None` for odd-length lists).
3. Model panics and re-edits with wrong indentation, making things worse.
4. Token count mushrooms across retries.

**Root cause of the aider vs opencode performance gap on T2/T3:** aider uses a SEARCH/REPLACE
block format — the model generates the complete function (signature + docstring + body) as a
natural code block.  This gives the model full context, avoids JSON encoding, and eliminates
the indentation problem entirely.  Same model, same endpoint — aider succeeds because the
editing *format* is better suited to code generation.

**Fix:** AGENTS.md updated to instruct the model: for stub implementation tasks use `write` to
rewrite the complete file, not `edit` to replace individual lines.  `write` has the same
structural advantage as aider's SEARCH/REPLACE — the model generates the full file content
naturally, with correct indentation and no risk of truncating the body mid-line.

For small targeted changes (single identifier rename, one-line fix) `edit` is still better —
but the `oldString` should include the full function signature for reliable matching.

---

## 32. tool_choice heuristics don't scale — use an explicit done signal

**Problem:** The proxy manipulated `tool_choice` based on counts of edit/write/bash calls to
decide when the model was "done" and could exit with a text response.  Every tier required its
own tuned threshold, and any change to agent behaviour (e.g. switching from `edit` to `write`)
broke the heuristic and caused either early exits or infinite loops:

- threshold = 1 edit → model exits after first edit, never writes second function
- threshold = 3 edits → model uses `write` (1 call), threshold never reached, infinite `git push` loop
- threshold: `write >= 1` → exits after first `write`, misses second file in T3
- all variants break for real tasks with arbitrary file counts

**Root cause:** Proxy-side heuristics try to infer task completion from side effects (which
tools were called, how many times).  Only the model knows when the task is actually done.

**Fix:** Inject a `task_complete(summary)` tool into every tool list.  Keep
`tool_choice=required` forever — the model cannot escape with prose.  When `task_complete`
appears in history, switch to `tool_choice=none` (not `auto` — `auto` still allows tool
calls).  Session ends with a forced text-only response.

**Implementation detail:** opencode cannot execute unknown tools.  It stores them in
conversation history as `name='invalid'`.  The proxy detects completion by checking for
`name in ("task_complete", "invalid")` — not just `"task_complete"`.

**Rule:** Give the model an explicit exit mechanism rather than inferring completion.
Any heuristic that counts tool calls is fragile and task-specific.

---

## 33. `tool_choice=auto` does not stop a looping model — use `none`

**Problem:** After detecting task completion, the proxy switched to `tool_choice=auto`.  The
assumption was that `auto` would let the model choose to stop.  Instead the model — which had
been in a `required` loop calling `git push` — continued calling `bash` on every turn even in
`auto` mode.  The loop continued until the agent timeout killed the sandbox.

**Root cause:** `tool_choice=auto` means "the model MAY call a tool."  It does not stop a
model that is already in a repetitive calling pattern.  A 32B model trained to call tools
will default to calling tools even when permitted not to.

**Fix:** Use `tool_choice=none` after the done signal.  `none` is the Chat Completions API
value that explicitly prohibits any tool call on that turn, forcing the model to generate
a plain-text response.  This is the only reliable way to break a looping model and extract
a closing summary.

**Rule:** To stop a model that is looping: `tool_choice=none`, not `auto`.
