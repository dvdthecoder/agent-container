# Building Agent Context

How to give coding agents the knowledge they need to succeed — without blowing the token budget.

---

## The problem: agents start cold

Every run today receives only a bare task string:

```
Fix the off-by-one bug in sum_to_n() in mathlib.py.
```

The agent has no knowledge of:

- Coding conventions in this repo ("use dataclasses, not dicts")
- Architecture constraints ("never import Modal in agent/")
- Acceptance criteria ("tests in test_mathlib.py must pass")
- Which files are relevant (costs a tool call to discover)
- What not to touch ("do not modify the public API")

Without this, every run opens with exploratory tool calls — `list`, `read`, `glob` — just to orient. Each of those turns costs a full round-trip through the 26k-token opencode prompt overhead. A two-turn exploration phase adds ~52k tokens in wasted context before a single line of code is written.

---

## The principle

Andrej Karpathy's core insight on agentic engineering (2025):

> "Don't tell it what to do — give it success criteria and watch it go."

The critical shift is from **imperative** ("call this function, then write that") to **declarative** ("here is what done looks like"). An agent given clear acceptance criteria can self-verify and self-correct. An agent given only a task description has to infer the done condition, often incorrectly.

His minimal agent loop pattern: **one editable file, one objective metric, fixed time limit**. We already have the time limit (`timeout_agent`). We need the objective metric (acceptance criteria) and the conventions (so the agent doesn't spend turns discovering them).

---

## The constraint: frugal token budget

Current opencode prompt overhead: **~26k tokens per run** just from framework (tool schemas, message history). Adding a 10k-token knowledge dump is a 40% cost increase with no guaranteed quality gain.

The design must be **selective and cheap**:

| Constraint | Rule |
|---|---|
| No full-document injection | Inject only what the agent will actually use |
| No embedding / retrieval infrastructure | Plain files, plain text, no vector DBs |
| Knowledge must amortise against gains | If it eliminates one tool-call turn, it pays for itself |
| Opt-in, not opt-out | No automatic injection of large files |

### Why context is net-negative in token cost

For **opencode** specifically, eliminating one tool-call turn saves an entire round of context accumulation:

```
Turn N prompt tokens ≈ 26k (schemas) + accumulated history
Turn N+1 prompt tokens ≈ 26k + history + tool result

Each extra turn: +26k tokens minimum
```

Injecting 500 tokens of conventions at turn 0 and eliminating one exploratory turn saves ~25.5k tokens net. The ROI is enormous for opencode.

For **aider**, the savings are smaller (single-shot, no turn accumulation) but still real — aider uses `--map-tokens 1024` for a repo map. Injecting the relevant file directly means aider skips the map phase and goes straight to editing.

---

## Three-layer design

### Layer 1 — `AGENTS.md` auto-injection

**What:** If the target repo's root contains an `AGENTS.md` file, the runner reads it from the cloned workspace and prepends it to the task prompt automatically. No configuration required.

**Why `AGENTS.md`:** This is the de facto standard as of August 2025 — adopted by OpenAI (88 instances in their main repo), Google, and the broader open-source community. Repos that use agentic tooling already have this file. We read it for free.

**Cost:** ~300–800 tokens (typical `AGENTS.md`). Zero extra tool calls.

**Benefit:** Eliminates 1–2 orientation turns. Tells the agent:
- Which files are relevant to common task types
- Coding conventions it must follow
- Architecture constraints (what not to touch)
- How to run the test suite

**Example `AGENTS.md`:**
```markdown
# AGENTS.md

## Coding conventions
- Type annotations on all public functions
- Use dataclasses for value objects, not plain dicts
- No shell=True in subprocess calls — use list form

## Architecture rules
- agent/ — sandbox-side code. No Modal imports here.
- modal/ — deployment code. No agent logic here.
- Never cross these boundaries.

## Testing
- Run: pytest tests/unit/ -q
- Unit tests must not touch the network or Modal
- Integration tests are in tests/integration/ and require Modal credentials
```

The runner implementation is ~10 lines:

```python
# In opencode_runner.py — prompt construction
agents_md = Path(CWD) / "AGENTS.md"
conventions = agents_md.read_text(encoding="utf-8").strip() if agents_md.exists() else ""
full_prompt = f"{conventions}\n\n---\n\n{TASK}" if conventions else TASK
```

---

### Layer 2 — Structured task spec (YAML)

**What:** An optional YAML file that provides machine-readable task structure alongside the human-readable task string.

**Format:**

```yaml
# task.yaml

task: |
  Fix the off-by-one bug in sum_to_n() in mathlib.py.
  The function uses range(1, n) but should use range(1, n + 1).

acceptance:
  # Agent knows it is done when ALL of these pass.
  tests: "pytest tests/unit/test_mathlib.py -q"
  diff_must_touch:
    - "mathlib.py"           # run fails if this file was not modified

constraints:
  - "Do not change the function signature"
  - "No new external dependencies"
  - "All new code must have type annotations"

security:
  scan_secrets: true
  allowed_paths:
    - "src/**"
    - "tests/**"
  deny_patterns:
    - "**/*.env"
    - "**/*.pem"
    - "**/secrets/**"

context:
  files:
    - "mathlib.py"           # injected verbatim into initial prompt
  # conventions auto-read from AGENTS.md — no need to specify here
```

**`AgentTaskSpec` extension:**

```python
@dataclass
class AgentTaskSpec:
    # ... existing fields ...
    spec_file: Path | None = None        # read task + structure from YAML
    acceptance_tests: str | None = None  # test command (overrides auto-detect)
    constraints: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    scan_secrets: bool = True
```

**How the composite prompt is built:**

```
[AGENTS.md content — if present in repo]

---

Task: Fix the off-by-one bug in sum_to_n()...

Constraints:
- Do not change the function signature
- No new external dependencies
- All new code must have type annotations

Acceptance: All tests in pytest tests/unit/test_mathlib.py -q must pass.

Relevant files:
--- mathlib.py ---
def sum_to_n(n):
    return sum(range(1, n))  # bug: should be range(1, n + 1)
```

The agent reads this on turn 0. It knows conventions, constraints, success criteria, and the relevant code — without spending a single tool call on discovery.

---

### Layer 3 — Diff scanner (security guardrails)

**What:** A pure-function scanner that runs on the collected diff before `push_and_pr`. No external services, no new dependencies — regex over the diff text.

**Why:** The agent has unrestricted bash access inside the sandbox. It can write anything. The scanner is the last gate before the change reaches GitHub.

**What it detects:**

| Category | Patterns | Severity | Action |
|---|---|---|---|
| Hardcoded secrets | `sk-`, `ghp_`, `AKIA[0-9A-Z]`, `password\s*=\s*["']` | Critical | Block PR, mark run failed |
| Shell injection risk | `shell=True`, `os.system(` | Warning | Annotate PR body |
| Dangerous eval | `eval(`, `exec(` | Warning | Annotate PR body |
| Weak crypto | `md5(`, `sha1(`, `DES`, `RC4` | Warning | Annotate PR body |
| Scope creep | Files modified outside `allowed_paths` | Critical | Block PR if `allowed_paths` set |
| New dependencies | New lines in `requirements.txt`, `pyproject.toml` | Info | Annotate PR for human review |

**Architecture:**

```python
# sandbox/diff_scanner.py

@dataclass
class ScanFinding:
    severity: str      # "critical" | "warning" | "info"
    category: str
    message: str
    line: str | None = None

@dataclass
class ScanResult:
    findings: list[ScanFinding]

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

def scan_diff(diff: str, spec: AgentTaskSpec) -> ScanResult:
    """Pure function — no I/O, no external calls."""
    ...
```

Critical findings suppress the PR (`create_pr=False`) and mark the run failed with reason `"security_scan_failed"`. Warnings and info are appended to the PR body under a collapsible `<details>` block.

This is the frugal version of what Snyk or Semgrep do at runtime — applied only to the agent's diff, not the whole codebase.

---

## Secure coding spec

Beyond diff scanning, agents should be given explicit secure coding guidance as part of the conventions injected via `AGENTS.md` or the task spec. Based on OWASP Top 10 translated to the patterns a coding agent is most likely to introduce:

```markdown
## Security (always follow)
- Never use shell=True in subprocess calls
- Never hardcode credentials, API keys, or tokens — use environment variables
- Never use eval() or exec() on untrusted input
- Use parameterised queries for any database operations (never f-string SQL)
- Use secrets.token_hex() or os.urandom() for random values, not random.random()
- When adding a new HTTP endpoint, validate and sanitise all inputs at the boundary
```

This adds ~150 tokens to the conventions block and prevents the most common agent-introduced vulnerabilities.

---

## AGENTS.md for this repo

To eat our own dogfood, `agent-container` itself should have an `AGENTS.md`:

```markdown
# AGENTS.md — agent-container

## Architecture rules
- agent/ — sandbox-side code. No Modal imports.
- modal/ — deployment code. No agent logic.
- sandbox/ — shared dataclasses (AgentTaskSpec, AgentTaskResult, SandboxConfig).
  No inference-layer imports.
- dashboard/ — FastAPI UI. Reads from SQLite via RunStore only.
- All cross-boundary imports are caught by scripts/check_container_imports.py.

## Coding conventions
- Type annotations on all public functions and methods
- Dataclasses for value objects (not plain dicts)
- No shell=True in subprocess calls
- No new external dependencies without discussion

## Testing
- Unit tests: pytest tests/unit/ -q     (no network, no Modal)
- Integration tests: pytest tests/integration/ (requires Modal credentials)
- Before committing: ruff check . && ruff format --check .

## Security
- Never hardcode credentials
- Never use eval() or exec()
- Input validation at system boundaries (CLI args, dashboard API, MCP tool inputs)
```

---

## Implementation plan

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | `AGENTS.md` auto-injection in both runners | Planned — [#154](https://github.com/dvdthecoder/agent-container/issues/154) |
| **Phase 2** | Structured YAML task spec + `AgentTaskSpec` extension | Planned — [#154](https://github.com/dvdthecoder/agent-container/issues/154) |
| **Phase 3** | Diff scanner — secret detection + scope guardrails | Planned — [#154](https://github.com/dvdthecoder/agent-container/issues/154) |
| **Phase 4** | `AGENTS.md` for this repo | Planned |

---

## Related

- [Agent Backends](agents.md) — aider vs opencode, when to use each
- [Architecture](architecture.md) — proxy token optimisations, opencode adapter
- [Lessons Learned](lessons-learned.md) — hard problems and what we learned
- [Analysis](analysis/index.md) — token cost measurements per model and backend
