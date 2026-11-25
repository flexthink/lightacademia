"""Authenticated loopback bridge to the desktop's native folder picker."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import secrets
import threading
from urllib.request import ProxyHandler, Request, build_opener


def pick_local_directory() -> str | None:
    url = os.environ.get("LIGHTACADEMIA_PICKER_URL")
    token = os.environ.get("LIGHTACADEMIA_PICKER_TOKEN")
    if not url or not token:
        raise OSError("The native directory picker is available in the desktop app. Launch Light Academia using its desktop launcher.")
    request = Request(url, data=b"", headers={"Authorization": f"Bearer {token}"}, method="POST")
    with build_opener(ProxyHandler({})).open(request, timeout=3600) as response:
        result = json.load(response)
    if "error" in result:
        raise OSError(result["error"])
    return result["directory"]


@contextmanager
def directory_picker_bridge(pick):
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/pick-directory" or not secrets.compare_digest(
                self.headers.get("Authorization", ""), f"Bearer {token}"
            ):
                self.send_error(403)
                return
            try:
                with lock:
                    directory = pick()
                result = {"directory": directory}
            except Exception as exc:
                result = {"error": str(exc)}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"LIGHTACADEMIA_PICKER_URL": f"http://127.0.0.1:{server.server_port}/pick-directory",
               "LIGHTACADEMIA_PICKER_TOKEN": token}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
