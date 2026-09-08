"""Faux serveurs HTTP, pour tester les adaptateurs reseau sans sortir de la machine."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar


class _Handler(BaseHTTPRequestHandler):
    routes: ClassVar[dict] = {}

    def log_message(self, *args):  # silence
        pass

    def _respond(self):
        path = self.path.split("?")[0]
        entry = None
        for suffix, value in self.routes.items():
            if path.endswith(suffix):
                entry = value
                break
        if entry is None:
            entry = (404, {"ok": False, "description": "not found"})
        status, body = entry
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _respond
    do_POST = _respond


class FakeServer:
    """Serveur jetable : `routes` associe un suffixe d'URL a (status, corps JSON)."""

    def __init__(self, routes: dict):
        handler = type("H", (_Handler,), {"routes": routes})
        self._srv = HTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self._thread.start()
        return f"http://127.0.0.1:{self._srv.server_address[1]}"

    def __exit__(self, *exc):
        self._srv.shutdown()
        self._srv.server_close()


TELEGRAM_HEALTHY = {
    "/getMe": (200, {"ok": True, "result": {"id": 42, "username": "hermes_bot"}}),
    "/getUpdates": (200, {"ok": True, "result": []}),
    "/getWebhookInfo": (200, {"ok": True, "result": {"url": ""}}),
}


def openai_reply(text: str = "pong") -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }
