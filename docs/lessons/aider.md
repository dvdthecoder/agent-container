# aider Backend

Lessons specific to the aider backend — configuration, diff quality, and token reporting.

---

## 5. aider's `--openai-api-base` flag mangles the URL

**Problem:** aider sets `OPENAI_API_BASE` from `--openai-api-base`. litellm reads
`OPENAI_API_BASE` and strips the `/v1` suffix, resulting in requests to `/chat/completions` — a
404 on every call.

**Fix:** Use `OPENAI_BASE_URL` (not `OPENAI_API_BASE`). The `/v1` suffix is guaranteed by
`SandboxConfig.env_for_backend("aider")` before the container starts.

---

## 6. Model asking clarifying questions instead of writing code

**Problem:** `--map-tokens 0` disabled the repo map. Without any file context the model replied
with questions ("where should I add this?") instead of editing files — empty diff, false failure.

**Fix:** Changed to `--map-tokens 1024`. Gives the model a concise file list and function
signatures without the full multi-minute scan.

---

## 7. `__pycache__` polluting PR diffs

**Problem:** The TESTING phase runs pytest which compiles `.pyc` files. These appeared in
`collect_diff` as the only changes — masking whether the agent wrote any real code.

**Fix:** After cloning, `git_ops.clone()` writes common build artifact patterns to
`.git/info/exclude` — local-only, never committed, keeps diffs clean.

---

## 8. aider token line appears on stdout, not stderr

**Problem:** Older aider printed `Tokens: X sent, Y received.` to stderr. Current versions
print it to stdout. `aider_runner.py` only scanned `is_stderr=True`, so every run logged
`prompt=0 completion=0 total=0`.

**Fix:** Scan both stdout and stderr for the token pattern.
