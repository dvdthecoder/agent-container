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
import sys
import time
import urllib.request
from pathlib import Path

# Bootstrap — load .env from project root.
_root = Path(__file__).parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass  # dotenv optional; rely on shell env

import os

POLL_INTERVAL = 30.0  # seconds between attempts (matches sandbox WARMING phase)


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
        except Exception:  # noqa: BLE001
            pass  # still cold — keep polling

        if time.monotonic() >= deadline:
            print(
                f"[deploy] ERROR: endpoint not ready after {timeout:.0f}s ({url})",
                flush=True,
            )
            sys.exit(1)

        print(f"[deploy] still waiting  elapsed={elapsed:.0f}s", flush=True)
        time.sleep(POLL_INTERVAL)


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
        help="Base URL to poll (default: OPENAI_BASE_URL from environment)",
    )
    args = parser.parse_args()

    base_url = args.url or os.environ.get("OPENAI_BASE_URL", "")
    if not base_url:
        print(
            "[deploy] ERROR: OPENAI_BASE_URL is not set. "
            "Set it in .env or pass --url.",
            flush=True,
        )
        sys.exit(1)

    wait(base_url, args.timeout)


if __name__ == "__main__":
    main()
