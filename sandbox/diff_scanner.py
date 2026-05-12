"""Diff scanner — inspect agent-written diffs before PR creation.

Runs three rule categories against the added lines in a unified diff:

  1. Secrets (error)    — API keys, tokens, hardcoded credentials.
                          Hard-fails the run: never push secrets.

  2. Scope (warning)    — files modified outside the context_files constraint
                          declared in the YAML task spec.  Logged but does not
                          block the run by default; the caller decides severity.

  3. OWASP (warning)    — high-signal insecure patterns in added code: eval(),
                          shell=True, os.system(), pickle.load(), unsafe yaml.load().
                          Logged as warnings; not blocking (too many false positives
                          in legitimate test/script code).

Usage
-----
    from sandbox.diff_scanner import scan_diff, ScanResult

    result = scan_diff(diff_text, context_files=["mathlib.py"])
    for v in result.violations:
        print(f"[{v.severity}] {v.rule}  {v.file}:{v.line_num}  {v.detail}")
    if not result.passed:
        raise RuntimeError("diff scanner blocked the run")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    severity: str  # "error" | "warning"
    rule: str
    file: str
    line_num: int
    line: str
    detail: str

    def __str__(self) -> str:
        preview = self.line[:120].strip()
        return f"[{self.severity}] {self.rule} — {self.file}:{self.line_num}: {preview}"


@dataclass
class ScanResult:
    passed: bool
    violations: list[Violation] = field(default_factory=list)

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    @property
    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

# Secrets — error severity.  Patterns chosen for high precision (few false
# positives) over recall.  Covered: AWS keys, GitHub tokens, OpenAI keys,
# Slack tokens, and generic hardcoded credential assignments.
_SECRET_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "aws-access-key"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "github-pat"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{59}"), "github-fine-grained-pat"),
    (re.compile(r"sk-[A-Za-z0-9]{48}"), "openai-api-key"),
    (re.compile(r"xoxb-[A-Za-z0-9\-]{24,}"), "slack-bot-token"),
    (
        re.compile(
            r"(?i)(password|passwd|secret|api_key|apikey|access_token|private_key)\s*="
            r'\s*["\'][^"\']{8,}["\']'
        ),
        "hardcoded-credential",
    ),
]

# OWASP — warning severity.  High-signal insecure patterns in added code.
_OWASP_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\beval\s*\("), "owasp-eval-injection"),
    (re.compile(r"subprocess[^#\n]*shell\s*=\s*True"), "owasp-shell-true"),
    (re.compile(r"\bos\.system\s*\("), "owasp-os-system"),
    (re.compile(r"\bpickle\.loads?\s*\("), "owasp-pickle-deserialisation"),
    (re.compile(r"\byaml\.load\s*\((?!.*Loader\s*=)"), "owasp-yaml-load-unsafe"),
]


# ---------------------------------------------------------------------------
# Diff parser
# ---------------------------------------------------------------------------


def _parse_diff(diff: str) -> list[tuple[str, list[tuple[int, str]]]]:
    """Parse a unified diff into ``(filename, [(line_num, line), ...])`` pairs.

    Only added lines (starting with ``+``, excluding ``+++`` headers) are
    returned.  Line numbers are 1-based within the added-line sequence for
    each file (not the original file line numbers — those require hunk header
    parsing which adds complexity for no benefit in pattern scanning).
    """
    files: list[tuple[str, list[tuple[int, str]]]] = []
    current_file = ""
    current_lines: list[tuple[int, str]] = []
    add_idx = 0

    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            if current_file:
                files.append((current_file, current_lines))
            current_file = raw[6:]  # strip "+++ b/"
            current_lines = []
            add_idx = 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            add_idx += 1
            current_lines.append((add_idx, raw[1:]))  # strip leading "+"

    if current_file:
        files.append((current_file, current_lines))

    return files


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_diff(
    diff: str,
    context_files: list[str] | None = None,
) -> ScanResult:
    """Scan *diff* for secrets, scope violations, and OWASP patterns.

    Args:
        diff:          Unified diff string (output of ``git diff``).
        context_files: Optional list of files the agent was allowed to modify
                       (from the YAML task spec ``context_files`` field).
                       Modifications to files outside this list are flagged as
                       scope warnings.  Pass ``None`` to skip scope checking.

    Returns:
        :class:`ScanResult` — ``passed`` is ``False`` if any *error*-severity
        violation was found (secrets).  Warnings do not affect ``passed``.
    """
    parsed = _parse_diff(diff)
    violations: list[Violation] = []

    # Normalise context_files to basenames for loose matching — the diff
    # path may be "src/lib/mathlib.py" while context_files says "mathlib.py".
    allowed_basenames: set[str] | None = None
    if context_files:
        allowed_basenames = {f.split("/")[-1].strip() for f in context_files if f.strip()}

    for fname, lines in parsed:
        basename = fname.split("/")[-1]

        # ── Scope check ──────────────────────────────────────────────────────
        if allowed_basenames is not None and basename not in allowed_basenames:
            violations.append(
                Violation(
                    severity="warning",
                    rule="scope-violation",
                    file=fname,
                    line_num=0,
                    line="",
                    detail=(
                        f"'{fname}' was modified but is not in context_files "
                        f"({', '.join(sorted(allowed_basenames))})"
                    ),
                )
            )

        for line_num, line in lines:
            # ── Secret scan ──────────────────────────────────────────────────
            for pattern, rule in _SECRET_RULES:
                if pattern.search(line):
                    violations.append(
                        Violation(
                            severity="error",
                            rule=rule,
                            file=fname,
                            line_num=line_num,
                            line=line,
                            detail=f"potential secret matched rule '{rule}'",
                        )
                    )

            # ── OWASP scan ───────────────────────────────────────────────────
            for pattern, rule in _OWASP_RULES:
                if pattern.search(line):
                    violations.append(
                        Violation(
                            severity="warning",
                            rule=rule,
                            file=fname,
                            line_num=line_num,
                            line=line,
                            detail=f"insecure pattern matched rule '{rule}'",
                        )
                    )

    has_errors = any(v.severity == "error" for v in violations)
    return ScanResult(passed=not has_errors, violations=violations)
