#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A tiny, dependency-free test server to simulate RustDesk hbbs "api-server" endpoints:

1) Client heartbeat:
   POST /api/heartbeat
   Body: JSON string (RustDesk sends a JSON string)
   Response (JSON object):
     - optionally contains "modified_at" (int) and "strategy" (object)

2) Admin API (management plane):
   POST /api/admin/devices/{id}/permanent-password
   Headers: X-Admin-Token: <token>
   Body: {"new_password": "..."}
   Response: {"ok": true, "device_id": "...", "modified_at": 123}

This server stores device strategies in memory only (lost on restart).
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple


def now_ms() -> int:
    return int(time.time() * 1000)


class StrategyStore:
    """
    In-memory store:
      device_id -> (modified_at_ms, config_options dict, extra dict)
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, Tuple[int, Dict[str, str], Dict[str, str]]] = {}

    def set_password(self, device_id: str, new_password: str) -> int:
        with self._lock:
            ts = now_ms()
            config_options = {"permanent-password": new_password}
            extra: Dict[str, str] = {}
            self._data[device_id] = (ts, config_options, extra)
            return ts

    def get_strategy_if_modified(self, device_id: str, client_modified_at: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            if device_id not in self._data:
                return None
            ts, config_options, extra = self._data[device_id]
            if ts == client_modified_at:
                return None
            return {
                "modified_at": ts,
                "strategy": {
                    "config_options": dict(config_options),
                    "extra": dict(extra),
                },
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "RustDeskTestHbbs/0.1"

    # injected at runtime
    store: StrategyStore
    admin_token: str

    def _read_json(self) -> Any:
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            # RustDesk sometimes posts a JSON string already; try parse raw as string then parse again
            s = raw.decode("utf-8", errors="replace").strip()
            try:
                inner = json.loads(s)
                return inner
            except Exception:
                raise

    def _send_json(self, code: int, obj: Any) -> None:
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_text(self, code: int, text: str) -> None:
        b = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt: str, *args: Any) -> None:
        # quieter logs with client address + path
        msg = fmt % args
        print(f"[{self.client_address[0]}] {self.command} {self.path} - {msg}")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_text(200, "ok")
            return
        self._send_text(404, "not found")

    def do_POST(self) -> None:
        if self.path == "/api/heartbeat":
            self._handle_heartbeat()
            return

        m = re.fullmatch(r"/api/admin/devices/([^/]+)/permanent-password", self.path)
        if m:
            self._handle_admin_set_password(m.group(1))
            return

        self._send_text(404, "not found")

    def _handle_admin_set_password(self, device_id: str) -> None:
        token = self.headers.get("X-Admin-Token", "")
        if not token or token != self.admin_token:
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return

        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"ok": False, "error": "json object required"})
            return

        new_password = body.get("new_password", "")
        if not isinstance(new_password, str) or not new_password:
            self._send_json(400, {"ok": False, "error": "new_password required"})
            return

        ts = self.store.set_password(device_id, new_password)
        self._send_json(200, {"ok": True, "device_id": device_id, "modified_at": ts})

    def _handle_heartbeat(self) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"error": "invalid json"})
            return

        # RustDesk sends a JSON object with fields like:
        # {"id": "...", "uuid": "...", "ver": 1440, "modified_at": 0, ...}
        if not isinstance(body, dict):
            self._send_json(200, {})
            return

        device_id = str(body.get("id", "") or "")
        client_modified_at = body.get("modified_at", 0)
        try:
            client_modified_at_i = int(client_modified_at)
        except Exception:
            client_modified_at_i = 0

        if not device_id:
            self._send_json(200, {})
            return

        rsp = self.store.get_strategy_if_modified(device_id, client_modified_at_i)
        self._send_json(200, rsp or {})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=21115)
    p.add_argument("--admin-token", default="devtoken")
    args = p.parse_args()

    store = StrategyStore()

    # Create a handler class bound with our store/token
    class BoundHandler(Handler):
        pass

    BoundHandler.store = store
    BoundHandler.admin_token = args.admin_token

    httpd = ThreadingHTTPServer((args.host, args.port), BoundHandler)
    print(f"Listening on http://{args.host}:{args.port}")
    print("Health: GET /health")
    print("Heartbeat: POST /api/heartbeat")
    print("Admin set password: POST /api/admin/devices/{id}/permanent-password (header X-Admin-Token)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()


