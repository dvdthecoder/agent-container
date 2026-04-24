"""Example 2 — run an agent task and watch progress via the WorkspaceStore.

Fires a run in a background thread (the same way the dashboard does) and
prints each lifecycle event as it arrives.  No HTTP server needed — the store
is shared in-process.

Usage:
    MODAL_TOKEN_ID=... MODAL_TOKEN_SECRET=... \\
    OPENAI_BASE_URL=... OPENAI_API_KEY=... \\
    python3 examples/run_with_dashboard.py
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from dashboard.store import WorkspaceStore
from sandbox.config import SandboxConfig
from sandbox.sandbox import ModalSandbox
from sandbox.spec import AgentTaskSpec

store = WorkspaceStore()
run_id = uuid.uuid4().hex[:12]

REPO = "https://github.com/dvdthecoder/agent-container-fixture"
TASK = "Fix the off-by-one bug in sum_to_n() in mathlib.py."

store.create_run(run_id=run_id, repo=REPO, task=TASK, backend="stub")


def on_event(event_type: str, payload: dict) -> None:
    store.push_event(run_id, event_type, payload)


def _run() -> None:
    spec = AgentTaskSpec(
        repo=REPO,
        task=TASK,
        backend="stub",  # stub agent — no LLM tokens spent
        create_pr=False,
        run_tests=False,
        timeout_seconds=120,
    )
    ModalSandbox(SandboxConfig.from_env()).run(spec, on_event=on_event)


executor = ThreadPoolExecutor(max_workers=1)
future = executor.submit(_run)

print(f"Run {run_id} started — polling for events…\n")
cursor = 0

while True:
    events = store.events_from(run_id, cursor)
    for ev in events:
        ts = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        if ev["type"] == "phase":
            print(f"[{ts}] PHASE → {ev['phase']}")
        elif ev["type"] == "log" and ev.get("text", "").strip():
            for line in ev["text"].strip().splitlines():
                print(f"[{ts}]   {line}")
        elif ev["type"] == "done":
            ok = ev.get("success")
            msg = "SUCCESS" if ok else f"FAILED — {ev.get('error', '')}"
            print(f"[{ts}] DONE  → {msg}")
    cursor += len(events)

    if store.is_terminal(run_id):
        break

    time.sleep(0.5)

future.result()
print("\nFinal state:", store.get_run(run_id).phase)
