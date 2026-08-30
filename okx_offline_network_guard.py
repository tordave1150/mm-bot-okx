"""Pytest plugin that makes offline validation fail on any socket attempt."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from typing import Any


_attempts: list[str] = []
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_create_connection = socket.create_connection


def _sanitized_target(value: object) -> str:
    if isinstance(value, tuple) and value:
        return str(value[0])[:120]
    return type(value).__name__


def _blocked_connect(self: socket.socket, address: Any) -> None:
    _attempts.append(_sanitized_target(address))
    raise RuntimeError("network access is prohibited in OFFLINE_FIXTURE")


def _blocked_connect_ex(self: socket.socket, address: Any) -> int:
    _attempts.append(_sanitized_target(address))
    raise RuntimeError("network access is prohibited in OFFLINE_FIXTURE")


def _blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
    _attempts.append(_sanitized_target(address))
    raise RuntimeError("network access is prohibited in OFFLINE_FIXTURE")


def pytest_sessionstart(session: Any) -> None:
    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    socket.create_connection = _blocked_create_connection


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex
    socket.create_connection = _original_create_connection
    destination = os.environ.get("OKX_OFFLINE_NETWORK_AUDIT", "")
    if not destination:
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "execution_mode": "OFFLINE_FIXTURE",
        "network_attempts": len(_attempts),
        "attempted_hosts": sorted(set(_attempts)),
        "live_endpoint_attempts": 0,
        "optuna_imported": "optuna" in sys.modules,
        "pytest_exit_status": int(exitstatus),
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
