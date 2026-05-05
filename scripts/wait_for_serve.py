"""Poll the inference endpoint until it is ready.

Reads OPENAI_BASE_URL from .env (or the shell environment).
Exits 0 when GET /v1/models returns 200, exits 1 on timeout.

Usage
-----
    python3 scripts/wait_for_serve.py              # timeout 900s, 30s poll
    python3 scripts/wait_for_serve.py --timeout 600
    OPENAI_BASE_URL=https://... python3 scripts/wait_for_serve.py

Called automatically by `make deploy` after `modal deploy` returns.
The same 30-second poll interval is used here as in the WARMING phase
of sandbox runs — Modal's web_server proxy queues requests while the
container port is not yet open, so polling too frequently accumulates
a backlog of pending Modal function calls.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request
from pathlib import Path

# Bootstrap — load .env from project root.
_root = Path(__file__).parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:  # noqa: S110
    pass  # dotenv optional; rely on shell env

POLL_INTERVAL = 30.0  # seconds between attempts (matches sandbox WARMING phase)


def _update_env_file(base_url: str) -> None:
    """Rewrite OPENAI_BASE_URL in the project .env file."""
    env_path = _root / ".env"
    if not env_path.exists():
        print(f"[deploy] .env not found at {env_path} — skipping update", flush=True)
        return
    lines = env_path.read_text().splitlines(keepends=True)
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("OPENAI_BASE_URL="):
            new_lines.append(f"OPENAI_BASE_URL={base_url}\n")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"OPENAI_BASE_URL={base_url}\n")
    env_path.write_text("".join(new_lines))
    print(f"[deploy] .env updated: OPENAI_BASE_URL={base_url}", flush=True)


def wait(base_url: str, timeout: float) -> None:
    url = base_url.rstrip("/").rstrip("/v1").rstrip("/") + "/v1/models"
    deadline = time.monotonic() + timeout
    t0 = time.monotonic()

    print(f"[deploy] waiting for endpoint: {url}", flush=True)

    while True:
        elapsed = time.monotonic() - t0
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
                if resp.status == 200:
                    print(f"[deploy] endpoint ready  elapsed={elapsed:.0f}s", flush=True)
                    return
        except Exception:  # noqa: BLE001, S110
            pass  # still cold — keep polling

        if time.monotonic() >= deadline:
            print(
                f"[deploy] ERROR: endpoint not ready after {timeout:.0f}s ({url})",
                flush=True,
            )
            sys.exit(1)

        print(f"[deploy] still waiting  elapsed={elapsed:.0f}s", flush=True)
        time.sleep(POLL_INTERVAL)


def _url_for_app(app_name: str, base_url: str) -> str:
    """Derive the Modal web endpoint URL for *app_name* from the known *base_url*.

    Modal web endpoint URLs follow the pattern:
        https://{org}--{app-name}-{function-name}.modal.run

    Given the existing OPENAI_BASE_URL (which points at the prod app) we can
    extract the org slug and rebuild the URL for any other app name.

    Example:
        base_url  = "https://dvdthecoder--agent-container-serve-qwen2-5-coder-32b-serve.modal.run"
        app_name  = "agent-container-serve-qwen2-5-coder-7b"
        → "https://dvdthecoder--agent-container-serve-qwen2-5-coder-7b-serve.modal.run"
    """
    import re

    m = re.match(r"(https://[^-]+)--[^.]+\.modal\.run", base_url)
    if not m:
        raise ValueError(
            f"Cannot parse org slug from OPENAI_BASE_URL={base_url!r}. Pass --url explicitly."
        )
    org_prefix = m.group(1)  # e.g. "https://dvdthecoder"
    return f"{org_prefix}--{app_name}-serve.modal.run"


def main() -> None:
    parser = argparse.ArgumentParser(description="Wait for inference endpoint to be ready.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Maximum seconds to wait (default: 900)",
    )
    parser.add_argument(
        "--url",
        default="",
        help="Base URL to poll (overrides OPENAI_BASE_URL and --app-name)",
    )
    parser.add_argument(
        "--app-name",
        default="",
        help=(
            "Modal app name to derive the URL from (e.g. agent-container-serve-qwen2-5-coder-7b). "
            "Requires OPENAI_BASE_URL to be set so the org slug can be extracted. "
            "Ignored when --url is provided."
        ),
    )
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="After the endpoint is ready, write the resolved URL to OPENAI_BASE_URL in .env",
    )
    args = parser.parse_args()

    env_url = os.environ.get("OPENAI_BASE_URL", "")

    if args.url:
        base_url = args.url
    elif args.app_name:
        if not env_url:
            print(
                "[deploy] ERROR: --app-name requires OPENAI_BASE_URL to be set "
                "(needed to extract the Modal org slug).",
                flush=True,
            )
            sys.exit(1)
        try:
            base_url = _url_for_app(args.app_name, env_url)
        except ValueError as exc:
            print(f"[deploy] ERROR: {exc}", flush=True)
            sys.exit(1)
    else:
        base_url = env_url

    if not base_url:
        print(
            "[deploy] ERROR: OPENAI_BASE_URL is not set. "
            "Set it in .env or pass --url / --app-name.",
            flush=True,
        )
        sys.exit(1)

    wait(base_url, args.timeout)

    if args.update_env:
        _update_env_file(base_url)


if __name__ == "__main__":
    main()
