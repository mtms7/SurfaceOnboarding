"""Serve the static UI preview on loopback for an SSH-tunnel-only review.

This is deliberately separate from the onboarding application and must never be
installed as a service. It serves no live data, takes no actions, accepts no
host override, and binds only to 127.0.0.1.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys

from integration.onboarding.ui_preview import render_dashboard_detail, render_dashboard_preview


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8001
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def preview_response(path: str) -> tuple[int, bytes, str]:
    """Return only the preview page, a minimal health response, or a 404."""
    if path == "/":
        return 200, render_dashboard_preview().encode("utf-8"), "text/html; charset=utf-8"
    if path == "/health":
        return 200, b'{"status":"static_preview_only"}', "application/json"
    if path.startswith("/co/"):
        detail = render_dashboard_detail(path.removeprefix("/co/"))
        if detail is not None:
            return 200, detail.encode("utf-8"), "text/html; charset=utf-8"
    return 404, b"not found\n", "text/plain; charset=utf-8"


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "SurfacePreview"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - required stdlib handler name
        status, body, content_type = preview_response(self.path)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        """Do not retain request paths or SSH-tunnel metadata in terminal logs."""


def parse_port(arguments: list[str]) -> int:
    if len(arguments) > 1:
        raise ValueError("usage: serve_loopback_preview.py [PORT]")
    if not arguments:
        return DEFAULT_PORT
    try:
        port = int(arguments[0])
    except ValueError as exc:
        raise ValueError("preview_port_must_be_integer") from exc
    if not 1024 <= port <= 65535:
        raise ValueError("preview_port_out_of_range")
    return port


def main(arguments: list[str]) -> int:
    try:
        port = parse_port(arguments)
    except ValueError as exc:
        print(str(exc))
        return 2
    server = ThreadingHTTPServer((LOOPBACK_HOST, port), PreviewHandler)
    print(f"static_preview_listening_on_loopback_port_{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
