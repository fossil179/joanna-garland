#!/usr/bin/env python3
"""Local tracker for Joanna Garland's tournament schedule and results."""

from __future__ import annotations

import json
import traceback
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from refresh import DATA_PATH, build

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
HOST = "127.0.0.1"
PORT = 8765


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/schedule":
            self.serve_data()
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/refresh":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        try:
            payload = build()
            body = json.dumps(payload).encode("utf-8")
            status = 200
        except Exception as error:  # noqa: BLE001 - surface the refresh failure to the page
            traceback.print_exc()
            body = json.dumps({"error": str(error)}).encode("utf-8")
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_data(self):
        if not DATA_PATH.exists():
            body = json.dumps({"error": "No schedule saved yet. Use Update."}).encode("utf-8")
            status = 404
        else:
            body = DATA_PATH.read_bytes()
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith("GET /api"):
            return
        super().log_message(fmt, *args)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Joanna Garland tracker: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
