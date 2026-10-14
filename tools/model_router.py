#!/usr/bin/env python3
"""
model_router.py — one OpenAI-compatible endpoint in front of several engines.

It:
  - exposes every backend's models to a client's picker (GET /v1/models merges backends),
  - routes each chat request to the engine that serves that model (by name),
  - NORMALIZES vLLM's stream so strict clients (GitHub Copilot BYOK) can read it:
      * removes "tool_calls":[] from content deltas (vLLM adds it; canonical
        OpenAI omits it; Copilot treats the empty array as a tool-call chunk and
        drops the text -> "Sorry, no response was returned").
      * drops the trailing usage-only chunk whose "choices" is an empty list.
  - streams replies through UNBUFFERED so time-to-first-token stays honest.

Runs next to the client. Remote engines are reached through SSH local-forwards, e.g.
  ssh -L 8000:localhost:8000 -L 8080:localhost:8080 <node-a>

Routing map (edit MODEL_ROUTES to add/rename models):
  qwen          -> 127.0.0.1:4000  (LiteLLM gateway in front of vLLM)
  gemma-4-coder -> 127.0.0.1:8080  (llama.cpp)

Safety (by construction): binds 127.0.0.1 only; forwards only to 127.0.0.1;
no sudo; no installs (stdlib only); does NOT log request/response bodies.

Usage:
  python model_router.py --selftest     # verify with fake backends, no real engine
  python model_router.py                # serve on 127.0.0.1:8001
  python model_router.py --listen-port 8001
"""

import argparse
import http.client
import http.server
import json
import socket
import sys
import threading
import time

LISTEN_HOST = "127.0.0.1"          # hardcoded: never bind to 0.0.0.0
BACKEND_HOST = "127.0.0.1"         # hardcoded: backends are localhost (remote ones via tunnel)
DEFAULT_LISTEN_PORT = 8001
BACKEND_TIMEOUT_S = 300            # a streamed reply can take minutes
CLIENT_TIMEOUT_S = 120             # evict a stalled/slow client (slowloris guard)
MODELS_PROBE_TIMEOUT_S = 5         # per-backend timeout when building /v1/models
RELAY_CHUNK = 8192                 # max bytes per relay read (read1 returns less if that's all)
FORWARD_HEADERS = ("Content-Type", "Authorization", "Accept")

# model name -> backend port on 127.0.0.1. THE one knob you edit to add a model.
MODEL_ROUTES = {
    "qwen": 4000,                               # -> LiteLLM gateway (which routes qwen -> vLLM)
    "gemma-4-coder": 8080,                      # llama.cpp (direct)
}


class _Server(http.server.ThreadingHTTPServer):
    # Default listen backlog (5) overflows when a client opens many sockets at once.
    request_queue_size = 512
    daemon_threads = True
    allow_reuse_address = True


# ----------------------------------------------------------------- normalization

def normalize_sse_line(line):
    """Transform one raw SSE line (bytes, no trailing newline).
    Returns bytes to forward, or None to DROP the line.
    - drop a chunk whose choices == []  (vLLM usage-only chunk)
    - strip tool_calls from any delta where it == []  (vLLM noise Copilot chokes on)
    - anything we can't parse is forwarded unchanged (never block an unknown chunk).
    """
    s = line.strip()
    if not s.startswith(b"data:"):
        return line                      # blank lines / comments: pass through
    payload = s[5:].strip()
    if not payload or payload == b"[DONE]":
        return line
    try:
        obj = json.loads(payload)
    except Exception:
        return line                      # unparseable: pass through untouched
    ch = obj.get("choices")
    if isinstance(ch, list):
        if len(ch) == 0:
            return None                  # drop usage-only chunk
        modified = False
        for c in ch:
            d = c.get("delta") if isinstance(c, dict) else None
            if isinstance(d, dict) and d.get("tool_calls") == []:
                del d["tool_calls"]
                modified = True
        if modified:
            return b"data: " + json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return line


# ----------------------------------------------------------------- handler

def make_handler(routes):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = CLIENT_TIMEOUT_S

        def log_message(self, *a):
            pass

        # -- GET: only /v1/models (merged) -----------------------------------
        def do_GET(self):
            if self.path.endswith("/models"):
                self._handle_models()
            else:
                self.send_error(404, "model_router serves GET /v1/models only")

        # -- POST: only /v1/chat/completions (routed + normalized) -----------
        def do_POST(self):
            if self.path.endswith("/chat/completions"):
                self._handle_chat()
            else:
                self.send_error(404, "model_router serves POST /v1/chat/completions only")

        def _handle_models(self):
            # Present each routable model whose backend answers /v1/models with 200.
            # A down backend is simply omitted (its model won't show in the picker).
            data = []
            for mid, port in routes.items():
                try:
                    conn = http.client.HTTPConnection(BACKEND_HOST, port,
                                                       timeout=MODELS_PROBE_TIMEOUT_S)
                    auth = self.headers.get("Authorization")
                    probe_hdrs = {"Authorization": auth} if auth else {}
                    conn.request("GET", "/v1/models", headers=probe_hdrs)
                    r = conn.getresponse()
                    up = (r.status == 200)
                    r.read()
                    conn.close()
                except Exception:
                    up = False
                if up:
                    data.append({"id": mid, "object": "model", "owned_by": "local"})
            payload = json.dumps({"object": "list", "data": data}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(payload)

        def _handle_chat(self):
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                self.send_error(411, "chunked request bodies not supported (send Content-Length)")
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""

            try:
                model = json.loads(body).get("model")
            except Exception:
                model = None
            port = routes.get(model)
            if port is None:
                self.send_error(404, "unknown model %r; known: %s"
                                % (model, ", ".join(routes)))
                return

            fwd = {h: self.headers[h] for h in FORWARD_HEADERS if h in self.headers}
            conn = http.client.HTTPConnection(BACKEND_HOST, port, timeout=BACKEND_TIMEOUT_S)
            try:
                conn.request("POST", self.path, body, fwd)
                resp = conn.getresponse()
            except Exception as exc:
                conn.close()
                self.send_error(502, "backend %d unreachable: %s" % (port, type(exc).__name__))
                return

            try:
                self.send_response(resp.status)
                ctype = resp.getheader("Content-Type")
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("X-Backend-Port", str(port))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self._relay(resp, ctype or "")
            except Exception:
                pass                       # client hung up, or backend died mid-stream
            finally:
                conn.close()

        def _relay(self, resp, ctype):
            # Non-streaming JSON: straight unbuffered passthrough.
            if "event-stream" not in ctype.lower():
                while True:
                    chunk = resp.read1(RELAY_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                return
            # SSE: normalize line by line, forwarding each as soon as it's complete.
            buf = b""
            while True:
                chunk = resp.read1(RELAY_CHUNK)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    out = normalize_sse_line(line)
                    if out is not None:
                        self.wfile.write(out + b"\n")
                        self.wfile.flush()
            if buf:                        # trailing partial (rare): forward raw
                self.wfile.write(buf)
                self.wfile.flush()

    return Handler


def serve(listen_port):
    httpd = _Server((LISTEN_HOST, listen_port), make_handler(MODEL_ROUTES))
    print("model_router listening on http://%s:%d" % (LISTEN_HOST, listen_port))
    for mid, port in MODEL_ROUTES.items():
        print("  %-28s -> %s:%d" % (mid, BACKEND_HOST, port))
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        httpd.shutdown()


# ----------------------------------------------------------------- self-test

STREAM_GAP_S = 0.5     # fake backend pauses after token #1; exposes a buffering proxy.


def start_fake_backend(model_id):
    """A fake OpenAI engine: serves /v1/models with its id, and a chat stream that
    tags its first token with its id (routing proof), includes tool_calls:[] in
    content deltas + a trailing empty-choices usage chunk (normalization targets),
    and pauses mid-stream (buffering proof)."""
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            payload = json.dumps({"object": "list",
                                  "data": [{"id": model_id, "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            first = {"choices": [{"index": 0,
                                  "delta": {"content": "MODEL[%s]" % model_id, "tool_calls": []},
                                  "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(first).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(STREAM_GAP_S)
            mid = {"choices": [{"index": 0, "delta": {"content": " hi", "tool_calls": []}}]}
            self.wfile.write(b"data: " + json.dumps(mid).encode() + b"\n\n")
            self.wfile.flush()
            stop = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            self.wfile.write(b"data: " + json.dumps(stop).encode() + b"\n\n")
            self.wfile.flush()
            usage = {"choices": [], "usage": {"completion_tokens": 2}}
            self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\n")
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *a):
            pass

    srv = _Server((LISTEN_HOST, 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _models_through(pport):
    conn = http.client.HTTPConnection(LISTEN_HOST, pport, timeout=10)
    conn.request("GET", "/v1/models")
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, json.loads(data)


def _chat_through(pport, model):
    conn = http.client.HTTPConnection(LISTEN_HOST, pport, timeout=20)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions",
                 json.dumps({"model": model,
                             "messages": [{"role": "user", "content": "hi"}],
                             "stream": True}),
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    status = resp.status
    raw = b""
    content = b""
    ttfb = None
    saw_done = False
    if status == 200:
        while True:
            line = resp.readline(65536)
            if not line:
                break
            raw += line
            s = line.strip()
            if not s.startswith(b"data:"):
                continue
            p = s[5:].strip()
            if p == b"[DONE]":
                saw_done = True
                break
            if ttfb is None:
                ttfb = time.perf_counter() - t0
            try:
                o = json.loads(p)
                for c in o.get("choices", []):
                    d = c.get("delta") or {}
                    if d.get("content"):
                        content += d["content"].encode()
            except Exception:
                pass
    conn.close()
    return status, raw, content, saw_done, ttfb


def selftest():
    print("SELF-TEST: two fake backends behind model_router ...")
    a = start_fake_backend("model-A")
    b = start_fake_backend("model-B")
    pa, pb = a.server_address[1], b.server_address[1]

    # a free port we leave closed => a deliberately-dead backend
    s = socket.socket()
    s.bind((LISTEN_HOST, 0))
    dead = s.getsockname()[1]
    s.close()

    routes = {"model-A": pa, "model-B": pb, "model-dead": dead}
    proxy = _Server((LISTEN_HOST, 0), make_handler(routes))
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    pport = proxy.server_address[1]

    problems = []

    # (1) /v1/models merge — both live models present, dead one omitted.
    mstatus, mbody = _models_through(pport)
    ids = sorted(m.get("id") for m in mbody.get("data", []))
    if mstatus != 200:
        problems.append("GET /v1/models returned %s" % mstatus)
    if ids != ["model-A", "model-B"]:
        problems.append("merged model list wrong: %s (expected model-A, model-B)" % ids)

    # (2)+(3) routing + normalization + unbuffered, for each live model.
    for mid in ("model-A", "model-B"):
        status, raw, content, done, ttfb = _chat_through(pport, mid)
        if status != 200:
            problems.append("%s: chat returned %s" % (mid, status))
            continue
        if ("MODEL[%s]" % mid).encode() not in content:
            problems.append("%s: routed to wrong backend (content=%r)" % (mid, content))
        if b"tool_calls" in raw:
            problems.append("%s: tool_calls NOT stripped from stream" % mid)
        if b"usage" in raw:
            problems.append("%s: empty-choices usage chunk NOT dropped" % mid)
        if not done:
            problems.append("%s: stream did not reach [DONE]" % mid)
        if ttfb is None or ttfb >= STREAM_GAP_S * 0.5:
            problems.append("%s: first token took %s s (>= %.2fs) -> BUFFERING, not streaming"
                            % (mid, ttfb, STREAM_GAP_S * 0.5))

    # (4) unknown model -> 404.
    st_unknown = _chat_through(pport, "no-such-model")[0]
    if st_unknown != 404:
        problems.append("unknown model returned %s, expected 404" % st_unknown)

    # (5) dead backend -> 502.
    st_dead = _chat_through(pport, "model-dead")[0]
    if st_dead != 502:
        problems.append("dead backend returned %s, expected 502" % st_dead)

    proxy.shutdown()
    a.shutdown()
    b.shutdown()

    if problems:
        print("SELF-TEST FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("SELF-TEST PASSED — models merged + routed by name + tool_calls:[] stripped + "
          "usage chunk dropped + streamed UNBUFFERED + unknown->404 + dead->502.")
    return 0


# --------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="Model-routing proxy "
                                 "(route by model + normalize vLLM stream).")
    ap.add_argument("--selftest", action="store_true",
                    help="verify with fake backends, then exit")
    ap.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT,
                    help="port to serve on (default %d)" % DEFAULT_LISTEN_PORT)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    if not (1 <= args.listen_port <= 65535):
        ap.error("--listen-port must be 1..65535")
    serve(args.listen_port)


if __name__ == "__main__":
    main()
