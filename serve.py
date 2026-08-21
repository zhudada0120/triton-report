#!/usr/bin/env python3
import argparse
import os
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Start local dev server for vllm-report")
    parser.add_argument("--port", type=int, default=8765, help="Port number (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/site/index.html"
    print(f"Serving at {url}")
    print("Press Ctrl+C to stop")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped")
        server.server_close()


if __name__ == "__main__":
    main()