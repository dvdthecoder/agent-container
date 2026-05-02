#!/usr/bin/env python3
"""CI guard: no file under modal/ or agent/ may import a dev-only package.

Dev-only packages are those listed exclusively in
[project.optional-dependencies.dev] in pyproject.toml.

Exit 0 — clean.
Exit 1 — violations found (printed to stdout).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Canonical package name → set of Python import names that indicate that
# package is being used.  Only packages from [project.optional-dependencies.dev]
# are listed here; runtime dependencies are intentionally omitted.
_DEV_PACKAGES: dict[str, set[str]] = {
    "pytest": {"pytest"},
    "pytest-asyncio": {"pytest_asyncio"},
    "pytest-httpx": {"pytest_httpx"},
    "ruff": {"ruff"},
    "bandit": {"bandit"},
    "pip-audit": {"pip_audit"},
    "detect-secrets": {"detect_secrets"},
    "pre-commit": {"pre_commit"},
    "mkdocs-material": {"mkdocs", "mkdocs_material", "material"},
}

# Flat map: import-name → package-name for fast lookup
_IMPORT_TO_PACKAGE: dict[str, str] = {
    imp: pkg for pkg, imps in _DEV_PACKAGES.items() for imp in imps
}

# Directories to scan (relative to repo root)
_SCAN_DIRS = ["modal", "agent"]

# Repo root is one directory above this script
_REPO_ROOT = Path(__file__).parent.parent


def _extract_top_level_imports(path: Path) -> list[str]:
    """Return the top-level module names imported by *path*.

    Only the first dotted component is returned so that
    ``from pytest_asyncio.plugin import ...`` → ``pytest_asyncio``.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def main() -> int:
    violations: list[str] = []

    for scan_dir in _SCAN_DIRS:
        base = _REPO_ROOT / scan_dir
        if not base.is_dir():
            print(
                f"[check_container_imports] warning: {scan_dir}/ not found — skipping",
                file=sys.stderr,
            )
            continue
        for py_file in sorted(base.rglob("*.py")):
            imported_names = _extract_top_level_imports(py_file)
            for name in imported_names:
                pkg = _IMPORT_TO_PACKAGE.get(name)
                if pkg:
                    rel = py_file.relative_to(_REPO_ROOT)
                    violations.append(f"  {rel}: imports '{name}' (dev-only package '{pkg}')")

    if violations:
        print("ERROR: container code imports dev-only packages.\n")
        print("The following files import packages that are only in")
        print("[project.optional-dependencies.dev] of pyproject.toml:\n")
        for v in violations:
            print(v)
        print()
        print("Fix: move the import to a tests/ file, or promote the dependency")
        print("to [project.dependencies] if it is genuinely needed at runtime.")
        return 1

    print("check_container_imports: OK — no dev-only imports found in modal/ or agent/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
