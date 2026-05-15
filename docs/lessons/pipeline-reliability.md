# Pipeline Reliability

Lessons about correctness gates between agent execution and recorded results — PR creation,
diff collection, test gating, and security scanning.

---

## 12. PR failures were completely silent

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

## 26. Diff scanning — catch secrets and scope drift before push

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

---

## 27. Test gating gap — test failures didn't block PR creation

**Problem:** The TESTING phase ran `pytest` and stored the `SuiteResult`, but `result.success`
was determined solely by the agent's exit code (`exit_code == 0`).  A run that produced broken
code, opened a PR, and reported ✅ — as long as the agent process exited cleanly.

Discovered during Tier 2/3 analysis runs (2026-05-13): opencode PRs #49 and #51 both contained
broken code but were reported as successes.

**Fix (#158):** After `detect_and_run()`, raise `PhaseError("TESTING", ...)` when
`suite.failed > 0`.  `PhaseError` is caught by the existing error handler which sets
`success=False` and skips the PR phase.  `suite=None` (no test runner detected) stays
non-blocking.

**Rule:** Store results for observability AND gate the outcome on them.  Observability without
gating gives false confidence.

---

## 30. Cross-tier test pollution — pytest discovers all test files

**Problem:** The fixture repo has test files for all three tiers.  Running `pytest` with no
arguments discovers all of them.  Tier 2/3 stubs (`NotImplementedError`) and old naming
(`calc_*`) cause `test_statslib.py`, `test_geometry.py`, `test_shapes.py` to fail on import,
blocking Tier 1 runs even when the Tier 1 fix is correct.

Exposed after #158 (test gating): a correct Tier 1 fix was reported ❌ because unrelated
Tier 2/3 tests failed.

**Fix:** `AgentTaskSpec.test_command` — explicit test command overrides auto-detection.
`token_analysis.py` passes the tier-specific file(s):
- `tier1` → `pytest test_mathlib.py`
- `tier2` → `pytest test_statslib.py`
- `tier3` → `pytest test_geometry.py test_shapes.py`

**Rule:** When a repo has intentionally broken stubs or incomplete files (multi-tier fixture,
feature branches, scaffolding), always scope the test command explicitly.  Auto-detection
is for greenfield repos where all test files are expected to pass.
