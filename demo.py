#!/usr/bin/env python3
"""
demo.py - the whole serving path, on your machine, in about a minute.

    fake engine(s)  <--  lbproxy (round-robin)  <--  loadtest (N concurrent users)

No GPU, no model, no network, no installs: Python 3.9+ standard library only.

Each fake engine behaves like a real inference server in the way that matters
for capacity planning: it can only work on a fixed number of requests at once
(its "batch slots"). Requests beyond that wait in a queue, which shows up as
time-to-first-token. The demo runs the same load against one engine and then
against two engines behind the round-robin proxy, so you can watch the second
node double throughput and collapse queueing - the same shape measured on real
hardware (see results/concurrency-ramp.csv).

Usage:
    python demo.py              # 16 concurrent users
    python demo.py --users 24
"""

import argparse
import contextlib
import http.server
import io
import json
import os
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
import lbproxy    # noqa: E402
import loadtest   # noqa: E402

SLOTS_PER_ENGINE = 4     # requests one fake engine decodes at the same time
PREFILL_S = 0.15         # simulated prompt processing before the first token
TOKENS = 40              # tokens per reply
TOKEN_GAP_S = 0.02       # time between tokens for one stream (≈50 tok/s per user)


def start_fake_engine():
    """An OpenAI-style streaming server with a hard concurrency limit."""
    slots = threading.BoundedSemaphore(SLOTS_PER_ENGINE)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            with slots:                                  # queue here when the engine is full
                time.sleep(PREFILL_S)
                for i in range(TOKENS):
                    chunk = {"choices": [{"delta": {"content": "tok%d " % i}}]}
                    self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(TOKEN_GAP_S)
            self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], '
                             b'"usage": {"completion_tokens": %d}}\n\n' % TOKENS)
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_proxy(backend_ports):
    """lbproxy's real handler, on a free port, in a background thread."""
    srv = lbproxy._Server(("127.0.0.1", 0), lbproxy.make_handler(lbproxy.RoundRobin(backend_ports)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def measure(proxy_port, users, label):
    """Run the real load tester through the proxy; return a small summary."""
    args = loadtest.build_parser().parse_args([
        "--port", str(proxy_port), "--label", label, "--users", str(users),
        "--rounds", "2", "--max-tokens", str(TOKENS), "--max-minutes", "5",
    ])
    with contextlib.redirect_stdout(io.StringIO()):      # keep the demo output short
        _, measured = loadtest.run_benchmark(args)
    rounds = [[r for r in rnd if r["ok"]] for rnd in measured]
    ok = [r for rnd in rounds for r in rnd]
    # All users start together (barrier), so a round lasts as long as its slowest request.
    agg = statistics.mean(sum(r["tokens"] for r in rnd) / max(r["total_s"] for r in rnd)
                          for rnd in rounds if rnd)
    return {
        "ok": len(ok), "total": sum(len(rnd) for rnd in measured),
        "agg": agg,
        "ttft_med": statistics.median(r["ttft_s"] for r in ok),
        "ttft_max": max(r["ttft_s"] for r in ok),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--users", type=int, default=16, help="concurrent users (default 16)")
    users = ap.parse_args().users
    if not 1 <= users <= loadtest.MAX_USERS:
        ap.error("--users must be between 1 and %d" % loadtest.MAX_USERS)

    engines = [start_fake_engine(), start_fake_engine()]
    ports = [e.server_address[1] for e in engines]
    print("Fake engines: %d batch slots each, ~%.0f tok/s per stream.\n"
          % (SLOTS_PER_ENGINE, 1 / TOKEN_GAP_S))

    rows = []
    for n in (1, 2):
        proxy = start_proxy(ports[:n])
        print("Running %d users against %d engine(s) ..." % (users, n), flush=True)
        rows.append((n, measure(proxy.server_address[1], users, "demo-%dnode" % n)))
        proxy.shutdown()

    print("\n%-9s %10s %18s %16s %12s" % ("engines", "requests", "aggregate tok/s",
                                          "median TTFT s", "max TTFT s"))
    for n, r in rows:
        print("%-9d %6d/%-3d %18.1f %16.2f %12.2f"
              % (n, r["ok"], r["total"], r["agg"], r["ttft_med"], r["ttft_max"]))
    one, two = rows[0][1], rows[1][1]
    print("\nSecond engine: %.2fx throughput, median time-to-first-token %.2fs -> %.2fs."
          % (two["agg"] / one["agg"], one["ttft_med"], two["ttft_med"]))
    for e in engines:
        e.shutdown()
    failed = any(r["ok"] != r["total"] for _, r in rows)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
