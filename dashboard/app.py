"""FastAPI application entry-point for the agent-container dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from dashboard.router import router

app = FastAPI(title="agent-container dashboard", version="0.1.0")

app.include_router(router, prefix="/api")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
