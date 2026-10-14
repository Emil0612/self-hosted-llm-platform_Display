#!/usr/bin/env python3
"""
lbproxy.py — tiny round-robin streaming load balancer for OpenAI-compatible LLM servers.

Presents ONE local endpoint and forwards each incoming request, round-robin, to one of
several backend vLLM servers — streaming the reply straight back token-by-token. This is
the "single address in front of the cluster" piece: clients talk to the proxy and never need
to know there are multiple nodes behind it.

In the two-node setup it runs on the first node and fans out to:
    localhost:8000  (vLLM on this node)
    localhost:8001  (vLLM on the second node, reached through an SSH tunnel)

Safety properties (by construction):
  - LISTENS on 127.0.0.1 only — never exposed to the network (hardcoded).
  - FORWARDS only to 127.0.0.1:<backend-port> — localhost only (ports are the only knob).
  - needs no sudo, installs nothing, uses only Python's standard library.
  - streams the backend reply through UNBUFFERED (read1 + flush) so it never delays or
    re-buffers tokens — TTFT measured through the proxy stays honest.
  - thread-per-connection (ThreadingHTTPServer) so concurrent streams don't block each other.
  - does NOT retry or fail over — a dead backend yields a 502 for that request, recorded as
    a failure rather than silently masked (matches the loadtest harness philosophy).
  - Ctrl+C stops it.

Usage:
  python3 lbproxy.py --selftest
      Verify the proxy against two built-in fake backends (round-robin + streaming). No real
      engine needed. Run this first.

  python3 lbproxy.py --listen-port 8080 --backends 8000,8001
      Serve on localhost:8080, fan out to localhost:8000 and localhost:8001.
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
BACKEND_TIMEOUT_S = 300            # generous: a streamed reply can take minutes
CLIENT_TIMEOUT_S = 120             # drop a stalled/slow client connection (slowloris guard)
MAX_INFLIGHT = 256                 # backpressure: shed load (503) above this many concurrent reqs
RELAY_CHUNK = 8192                 # max bytes per relay read (read1 returns less if that's all)
FORWARD_HEADERS = ("Content-Type", "Authorization", "Accept")


class _Server(http.server.ThreadingHTTPServer):
    # The default listen backlog (request_queue_size) is 5. A barrier-released benchmark slams
    # dozens of connections at the same instant, overflowing it -> some get "Connection reset by
    # peer". Raise it well above peak concurrency. Plus daemon threads + address reuse.
    request_queue_size = 512
    daemon_threads = True
    allow_reuse_address = True


class RoundRobin:
    """Thread-safe round-robin index over the backend ports."""
    def __init__(self, ports):
        self.ports = list(ports)
        self._i = 0
        self._lock = threading.Lock()

    def next(self):
        with self._lock:
            port = self.ports[self._i % len(self.ports)]
            self._i += 1
        return port


def make_handler(rr):
    inflight = threading.BoundedSemaphore(MAX_INFLIGHT)

    class Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.0 => reply is framed by connection-close; simplest correct streaming.
        protocol_version = "HTTP/1.0"
        timeout = CLIENT_TIMEOUT_S                          # evict a stalled/slow client connection

        def _proxy(self):
            # Backpressure: above MAX_INFLIGHT concurrent requests, shed load with 503 so a
            # runaway/buggy client can't pile up unbounded long-lived streaming threads. Normal
            # load (<=128) never hits this; it's purely a shared-box safety cap.
            if not inflight.acquire(blocking=False):
                self.send_error(503, "load balancer at capacity (%d in flight)" % MAX_INFLIGHT)
                return
            try:
                self._do_proxy()
            finally:
                inflight.release()

        def _do_proxy(self):
            port = rr.next()
            # We re-frame replies as HTTP/1.0 (connection-close), so we can't transparently
            # relay a CHUNKED request body. OpenAI clients send Content-Length; reject the rare
            # chunked-request case loudly instead of silently forwarding an empty body.
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                self.send_error(411, "chunked request bodies not supported (send Content-Length)")
                return
            # Read the full request body (bounded by the client's Content-Length).
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else None

            fwd = {h: self.headers[h] for h in FORWARD_HEADERS if h in self.headers}

            conn = http.client.HTTPConnection(BACKEND_HOST, port, timeout=BACKEND_TIMEOUT_S)
            try:
                conn.request(self.command, self.path, body, fwd)
                resp = conn.getresponse()
            except Exception as exc:                       # backend down / refused / timeout
                conn.close()
                self.send_error(502, "backend %d unreachable: %s" % (port, type(exc).__name__))
                return

            try:
                self.send_response(resp.status)
                ctype = resp.getheader("Content-Type")
                if ctype:
                    self.send_header("Content-Type", ctype)
                self.send_header("X-LB-Backend", str(port))  # which node served it (debug aid)
                self.end_headers()
                # Stream the reply through UNBUFFERED: read1 returns as soon as any data is
                # available, so tokens are forwarded the instant they arrive.
                while True:
                    chunk = resp.read1(RELAY_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                # Either the CLIENT hung up, or the BACKEND died mid-stream (e.g. IncompleteRead,
                # ConnectionReset). Stop relaying without crashing the handler thread. If the
                # backend died, the client's stream ends WITHOUT the final `data: [DONE]`, which
                # loadtest.py records as a failure ("stream ended before completion") — so the
                # truncation is surfaced honestly, never masked as a short success.
                pass
            finally:
                conn.close()

        def do_POST(self):
            self._proxy()

        def do_GET(self):
            self._proxy()

        def log_message(self, *a):                          # keep stdout clean under load
            pass

    return Handler


def serve(listen_port, backends):
    rr = RoundRobin(backends)
    httpd = _Server((LISTEN_HOST, listen_port), make_handler(rr))
    print("lbproxy listening on http://%s:%d  ->  backends %s"
          % (LISTEN_HOST, listen_port, ", ".join("%s:%d" % (BACKEND_HOST, p) for p in backends)))
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------- self-test

FAKE_TOKENS = 6
STREAM_GAP_S = 0.5       # the fake backend PAUSES mid-stream right after token #1; a buffering
                        # proxy would hide token #1 until the pause ends — caught by the TTFB check.


def start_fake_backend():
    """A fake OpenAI-streaming backend that (a) tags its first token with its own port so the
    test can attribute each request to a backend, and (b) pauses after that first token so the
    test can PROVE the proxy relays unbuffered."""
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            myport = self.server.server_address[1]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            first = {"choices": [{"delta": {"content": "PORT%d" % myport}}]}
            self.wfile.write(b"data: " + json.dumps(first).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(STREAM_GAP_S)          # the mid-stream pause that exposes buffering
            for i in range(FAKE_TOKENS):
                chunk = {"choices": [{"delta": {"content": " t%d" % i}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                             b'"usage":{"completion_tokens":%d}}\n\n' % (FAKE_TOKENS + 1))
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):
            pass

    srv = _Server((LISTEN_HOST, 0), Handler)            # port 0 = pick a free one
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _one_request_through(proxy_port):
    """Send one streaming request through the proxy. Returns (served_port, saw_done, ttfb_s),
    where ttfb_s is when the FIRST streamed token reached us (for the buffering check)."""
    conn = http.client.HTTPConnection(LISTEN_HOST, proxy_port, timeout=20)
    t0 = time.perf_counter()
    conn.request("POST", "/v1/chat/completions",
                 json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}),
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    served, saw_done, ttfb = None, False, None
    text = b""
    while True:
        line = resp.readline(65536)
        if not line:
            break
        line = line.strip()
        if not line.startswith(b"data:"):
            continue
        payload = line[5:].strip()
        if payload == b"[DONE]":
            saw_done = True
            break
        if ttfb is None:
            ttfb = time.perf_counter() - t0       # first real token arrived
        text += payload
    conn.close()
    marker = b"PORT"
    if marker in text:
        after = text.split(marker, 1)[1]
        digits = b""
        for ch in after:
            c = bytes([ch])
            if c.isdigit():
                digits += c
            else:
                break
        if digits:
            served = int(digits)
    return served, saw_done, ttfb


def selftest():
    print("SELF-TEST: two fake backends + the proxy, CONCURRENT requests ...")
    b1 = start_fake_backend()
    b2 = start_fake_backend()
    ports = [b1.server_address[1], b2.server_address[1]]
    proxy = _Server((LISTEN_HOST, 0), make_handler(RoundRobin(ports)))
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    pport = proxy.server_address[1]

    problems = []

    # (A) UNBUFFERED relay — ONE request, no concurrency, so timing is clean (no thread-jitter).
    # The backend emits token #1, then pauses STREAM_GAP_S. A streaming proxy delivers token #1
    # immediately (~0s); a buffering proxy withholds it until the whole reply is ready (>= gap).
    served0, done0, ttfb0 = _one_request_through(pport)
    if not done0:
        problems.append("single streaming request did not reach [DONE]")
    if ttfb0 is None or ttfb0 >= STREAM_GAP_S * 0.5:
        problems.append("first token took %s s (>= %.2fs) -> proxy is BUFFERING, not streaming"
                        % (ttfb0, STREAM_GAP_S * 0.5))

    # (B) CONCURRENCY + round-robin — fire n requests at once (barrier-released) so they truly
    # overlap. We check completion / balance / overlap, NOT per-request TTFB (too jittery here).
    n = 8
    results = [None] * n
    barrier = threading.Barrier(n)

    def worker(idx):
        barrier.wait()
        results[idx] = _one_request_through(pport)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    wall = time.perf_counter() - t0

    if any(r is None for r in results):
        problems.append("a worker never finished (possible deadlock/serialization)")
    done_results = [r for r in results if r is not None]
    served = [r[0] for r in done_results]
    dones = sum(1 for r in done_results if r[1])

    if dones != len(done_results):
        problems.append("only %d/%d concurrent replies reached [DONE]" % (dones, len(done_results)))
    if any(s is None for s in served):
        problems.append("some requests had no backend marker: %s" % served)
    used = set(s for s in served if s is not None)
    if used != set(ports):
        problems.append("not all backends used (round-robin broken): %s of %s" % (used, ports))
    else:
        for p in ports:
            c = sum(1 for s in served if s == p)
            if abs(c - n / len(ports)) > 1:
                problems.append("backend %d served %d (badly unbalanced)" % (p, c))
    # 8 overlapping ~STREAM_GAP_S requests must finish in ~one pause, not eight (= serialized).
    if wall > STREAM_GAP_S * 4:
        problems.append("8 concurrent requests took %.2fs -> looks serialized (expected ~%.2fs)"
                        % (wall, STREAM_GAP_S))

    proxy.shutdown()

    # DEAD BACKEND -> a clean 502, never a crash/hang/fake-success.
    s = socket.socket()
    s.bind((LISTEN_HOST, 0))
    dead_port = s.getsockname()[1]
    s.close()                                          # port now free => connections refused
    dproxy = _Server((LISTEN_HOST, 0), make_handler(RoundRobin([dead_port])))
    threading.Thread(target=dproxy.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection(LISTEN_HOST, dproxy.server_address[1], timeout=5)
        c.request("POST", "/v1/chat/completions", b"{}", {"Content-Type": "application/json"})
        status = c.getresponse().status
        c.close()
        if status != 502:
            problems.append("dead backend returned HTTP %s, expected 502" % status)
    except Exception as exc:
        problems.append("dead-backend request raised instead of returning 502: %r" % exc)
    finally:
        dproxy.shutdown()
        b1.shutdown()
        b2.shutdown()

    if problems:
        print("SELF-TEST FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("SELF-TEST PASSED — concurrent + round-robin balanced + streamed UNBUFFERED + "
          "dead-backend -> 502.")
    return 0


# --------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="Round-robin streaming load balancer for "
                                 "localhost OpenAI-compatible LLM servers.")
    ap.add_argument("--selftest", action="store_true",
                    help="verify against two built-in fake backends, then exit")
    ap.add_argument("--listen-port", type=int, default=8080, help="port to serve on (default 8080)")
    ap.add_argument("--backends", default="8000,8001",
                    help="comma-separated backend ports on 127.0.0.1 (default 8000,8001)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    try:
        backends = [int(p.strip()) for p in args.backends.split(",") if p.strip()]
    except ValueError:
        ap.error("--backends must be comma-separated port numbers, e.g. 8000,8001")
    if not backends:
        ap.error("need at least one backend port")
    for p in backends + [args.listen_port]:
        if not (1 <= p <= 65535):
            ap.error("ports must be between 1 and 65535")
    if args.listen_port in backends:
        ap.error("--listen-port must differ from the backend ports")

    serve(args.listen_port, backends)


if __name__ == "__main__":
    main()
