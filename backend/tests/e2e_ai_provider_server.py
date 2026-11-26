"""Isolated, metadata-only model-list fixture for the disposable E2E stack."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        elif self.path == "/v1/models":
            if self.headers.get("Authorization") not in {
                "Bearer synthetic-e2e-provider-key",
                "Bearer synthetic-replacement-key",
            }:
                body = b'{"error":{"message":"synthetic authentication failure"}}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = b'{"data":[{"id":"synthetic-nutrition-model","object":"model"}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            body = b"{}"
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        self.send_error(405)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
