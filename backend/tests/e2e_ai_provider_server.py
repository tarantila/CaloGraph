"""Isolated model-list and fixed-answer fixture for the disposable E2E stack."""

import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import sleep


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
        if self.path != "/v1/chat/completions":
            self.send_error(405)
            return
        if self.headers.get("Authorization") != "Bearer synthetic-e2e-provider-key":
            self.send_error(401)
            return
        self.connection.settimeout(5)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16 * 1024 * 1024:
                raise ValueError
            request = json.loads(self.rfile.read(size))
            messages = request["messages"]
            if (
                request["model"] != "synthetic-nutrition-model"
                or not isinstance(messages, list)
                or not all(isinstance(item, dict) for item in messages)
            ):
                raise ValueError
        except (ValueError, KeyError, TypeError, OSError):
            self.send_error(400)
            return
        # Fixed synthetic output only: never reflect or log supplied context.
        if messages and messages[-1].get("content") == "synthetic-cancel":
            sleep(3)
        answer = {
            "text": "Synthetische Testantwort zur Ernährung.",
            "conversation_title": "Ballaststoffzufuhr",
            "findings": [],
            "limitations": ["Synthetischer Test ohne Ernährungsbewertung."],
            "follow_up": [],
        }
        body = json.dumps({"choices": [{
            "message": {"role": "assistant", "content": json.dumps(answer)},
            "finish_reason": "stop",
        }]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
