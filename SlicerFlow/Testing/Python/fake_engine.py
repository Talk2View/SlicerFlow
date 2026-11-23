"""A scripted Talk2View engine for the end-to-end test. Standard library only.

It answers the routes a chat uses, records every request, and plays the chat the test
scripts: ``engine.script(turn, ...)`` queues the SSE chunks each following
``/messages`` or ``/resume`` request gets, in order. A turn that ends in ``tool_call(...)``
ends its stream without ``[DONE]``, as the engine does.
"""

import email.parser
import email.policy
import json
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG = {
    "default_llm_model": "m-fast",
    "default_stt_model": None,
    "default_image_model": None,
    "system_prompt": None,
    "allowed_llm_models": ["m-fast"],
    "allowed_stt_models": None,
    "gated_llm_models": None,
    "gated_stt_models": None,
    "web_search_enabled": False,
}
MODELS = [{"id": "m-fast", "object": "model", "created": 1, "owned_by": "t2v", "name": "Fast"}]
STOP = {"id": "c", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
DONE = "[DONE]"


def text(content):
    return {"id": "c", "choices": [{"index": 0, "delta": {"content": content}}]}


def tool_call(name, call_id, arguments):
    return {
        "id": "c",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        "interrupt": {"type": "tool_call", "tool_name": name, "tool_call_id": call_id, "arguments": arguments},
    }


class _Server(ThreadingHTTPServer):
    """Quiet about a client that went away. A test that stops the chat while an upload is
    held open closes the socket under the answer, and the traceback ``socketserver`` would
    print for it is noise, not a failure."""

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)


class FakeEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = []  # (method, path, JSON body or None)
        self.uploads = []  # {"id", "filename", "mime_type", "data"}
        self.tools = []  # tool names in the last /v1/tools/register
        self.session_id = str(uuid.uuid4())
        self.upload_started = threading.Event()  # an /v1/attachments body has been read
        self._upload_gate = None  # set by hold_uploads(): the answer waits for it
        self._turns = []
        self._server = _Server(("127.0.0.1", 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="fake-engine", daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:{}".format(self._server.server_address[1])

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        # shutdown() waits for serve_forever() to return, so it would never end on an
        # engine that was never started. The listening socket is closed either way, and a
        # second stop() is harmless.
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()

    def script(self, *turns):
        with self.lock:
            self._turns.extend(turns)

    def hold_uploads(self):
        """Keep every /v1/attachments answer on the wire until ``release_uploads()``, so a
        test chooses the moment something happens mid-upload rather than a sleep guessing
        it. The upload is recorded before the hold, as the real one is once it has arrived.
        """
        self._upload_gate = threading.Event()

    def release_uploads(self):
        if self._upload_gate is not None:
            self._upload_gate.set()

    def bodies(self, suffix):
        with self.lock:
            return [body for _method, path, body in self.requests if path.endswith(suffix)]

    def _next_turn(self):
        with self.lock:
            return self._turns.pop(0) if self._turns else [text("(no scripted turn)"), STOP, DONE]

    def _handler_class(self):
        engine = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _read(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _json(self, status, body=None):
                data = b"" if body is None else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _sse(self, chunks):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b": t2v stream open\n\n")
                for chunk in chunks:
                    payload = chunk if chunk == DONE else json.dumps(chunk)
                    self.wfile.write("data: {}\n\n".format(payload).encode("utf-8"))
                    self.wfile.flush()
                self.close_connection = True

            def _handle(self):
                raw = self._read()
                path = self.path.split("?", 1)[0]
                is_json = self.headers.get("Content-Type", "").startswith("application/json")
                body = json.loads(raw) if raw and is_json else None
                with engine.lock:
                    engine.requests.append((self.command, path, body))
                if path in ("/v1/auth/login", "/v1/auth/refresh"):
                    return self._json(200, {
                        "access_token": "access-" + uuid.uuid4().hex,
                        "refresh_token": "refresh-" + uuid.uuid4().hex,
                        "user": {"id": "user-1", "email": "user@example.com"},
                        "is_anonymous": False,
                    })
                if path == "/v1/auth/anonymous":
                    return self._json(200, {
                        "access_token": "access-" + uuid.uuid4().hex,
                        "refresh_token": "refresh-" + uuid.uuid4().hex,
                        "user": {"id": "guest-1", "email": None},
                        "is_anonymous": True,
                    })
                if not self.headers.get("Authorization", "").startswith("Bearer "):
                    return self._json(401, {"error": {"type": "authentication_error", "message": "Sign in."}})
                if path == "/v1/auth/logout":
                    return self._json(204)
                if path == "/v1/config":
                    return self._json(200, CONFIG)
                if path == "/v1/models":
                    return self._json(200, {"object": "list", "data": MODELS})
                if path == "/v1/account/warm":
                    return self._json(200, {"ok": True})
                if path == "/v1/sessions" and self.command == "POST":
                    return self._json(200, {"session_id": engine.session_id, "thread_id": "th-1", "model": "m-fast"})
                if path == "/v1/tools/register":
                    with engine.lock:
                        engine.tools = [tool["name"] for tool in (body or {}).get("tools", [])]
                    return self._json(200, {"registered": engine.tools, "count": len(engine.tools)})
                if path == "/v1/skills/register":
                    names = [skill["name"] for skill in (body or {}).get("skills", [])]
                    return self._json(200, {"registered": names, "count": len(names), "removed": [], "replaced": True})
                if path == "/v1/attachments":
                    return self._upload(raw)
                if re.fullmatch(r"/v1/sessions/[^/]+/(messages|resume)", path):
                    return self._sse(engine._next_turn())
                if re.fullmatch(r"/v1/sessions/[^/]+", path) and self.command == "DELETE":
                    return self._json(204)
                return self._json(404, {"error": {"type": "not_found", "message": path}})

            def _upload(self, raw):
                header = "Content-Type: {}\r\n\r\n".format(self.headers["Content-Type"]).encode("latin-1")
                message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(header + raw)
                part = next(message.iter_parts())
                record = {
                    "id": str(uuid.uuid4()),
                    "filename": part.get_filename(),
                    "mime_type": part.get_content_type(),
                    "size_bytes": len(part.get_payload(decode=True)),
                }
                with engine.lock:
                    engine.uploads.append(dict(record, data=part.get_payload(decode=True)))
                engine.upload_started.set()
                gate = engine._upload_gate
                if gate is not None:
                    gate.wait(30)
                try:
                    return self._json(200, record)
                except OSError:  # the chat was stopped while the answer was held
                    self.close_connection = True

            do_GET = do_POST = do_DELETE = _handle

        return Handler
