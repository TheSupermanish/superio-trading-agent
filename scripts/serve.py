"""Static file server for the dashboard.

`python -m http.server` is single-threaded: it serves one request at a time and
holds keep-alive connections open, so the page load blocks the snapshot fetch
the page makes on arrival and the dashboard sits on "Loading" forever.

This is the same thing, threaded, with caching turned off so a refreshed
snapshot is served rather than a stale 304.
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        # One line per request is noise at a 60 second refresh; errors still raise.
        pass


def main() -> None:
    directory = sys.argv[1] if len(sys.argv) > 1 else "."
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8088
    server = ThreadingHTTPServer(("0.0.0.0", port), partial(Handler, directory=directory))
    server.daemon_threads = True
    print(f"serving {directory} on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
