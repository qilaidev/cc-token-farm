#!/usr/bin/env python3
"""Local transparent forwarder: CC Switch -> Anthropic with cleaned OAuth headers."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://api.anthropic.com"
HOST, PORT = "127.0.0.1", 18999


class Handler(BaseHTTPRequestHandler):
    def _handle(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        auth = self.headers.get("Authorization") or self.headers.get("authorization")
        if not auth:
            self.send_error(401, "missing Authorization")
            return
        if not auth.lower().startswith("bearer "):
            auth = f"Bearer {auth}"
        headers = {
            "content-type": self.headers.get("Content-Type") or "application/json",
            "anthropic-version": self.headers.get("anthropic-version") or "2023-06-01",
            "Authorization": auth,
        }
        beta = self.headers.get("anthropic-beta")
        if beta:
            headers["anthropic-beta"] = beta
        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body or None, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read() if e.fp else b""
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            data = json.dumps({"error": str(e)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        self._handle()

    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"oauth_forwarder listening on http://{HOST}:{PORT} -> {UPSTREAM}", flush=True)
    httpd.serve_forever()
