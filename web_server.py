"""
web_server.py — Serves the HTML dashboard and a JSON state endpoint on
localhost. Runs in a daemon thread started from strategy.py, sharing memory
with the strategy through DashboardState.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from config import Config
from dashboard_state import DashboardState

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def build_app(state: DashboardState) -> FastAPI:
    """Build the FastAPI application with dashboard routes."""
    app = FastAPI(docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state", response_class=JSONResponse)
    def get_state() -> dict:
        return state.get_snapshot()

    return app


class WebServer:
    """Runs FastAPI/uvicorn in a background daemon thread.

    ``uvicorn.Server.run()`` creates its own asyncio event loop — this is
    fine inside a daemon thread and does not conflict with the WebSocket
    thread's own loop in strategy.py, since each thread has its own loop.
    """

    def __init__(self, config: Config, state: DashboardState):
        self.cfg = config
        self.state = state
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    def start(self) -> None:
        """Start the web server in a background daemon thread."""
        app = build_app(self.state)
        uv_config = uvicorn.Config(
            app,
            host=self.cfg.dashboard_host,
            port=self.cfg.dashboard_port,
            log_level="warning",   # keep uvicorn quiet — bot.log is the source of truth
        )
        self._server = uvicorn.Server(uv_config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="dashboard-http",
            daemon=True,
        )
        self._thread.start()

        url = f"http://{self.cfg.dashboard_host}:{self.cfg.dashboard_port}"
        logger.info("Dashboard available at %s", url)

        if self.cfg.dashboard_open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def stop(self) -> None:
        """Signal the web server to exit and wait for the thread to join."""
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
