#!/usr/bin/env python3
"""
loadtest.py — concurrency benchmark for OpenAI-compatible LLM servers.

Simulates N users sending chat requests at the same moment and measures,
for each request: time-to-first-token (TTFT), generation speed (tokens/sec),
and for each round: aggregate throughput across all users.

Safety properties (by construction):
  - talks ONLY to 127.0.0.1 — the target host is hardcoded, not an option
  - needs no sudo, installs nothing, uses only Python's standard library
  - writes ONLY new timestamped files inside its own results/ folder
  - every request has an idle timeout AND a hard cap; the run has a
    wall-clock cap as well
  - never retries; failures are recorded as results, not fought
  - normal foreground process: Ctrl+C stops it and prints partial results

Note on --api-key: the value is visible in shell history and `ps` on a
shared machine. Fine for a local test key; don't pass a real secret this way.

Usage:
  python3 loadtest.py --selftest
      Verifies the harness against a built-in fake LLM server. No real
      engine needed. Run this first, on any machine.

  python3 loadtest.py --port 8080 --label llamacpp-rocm-fp16 --users 4
      Benchmark a llama.cpp server on localhost:8080 with 4 simultaneous
      users (1 warm-up round + 2 measured rounds).

  python3 loadtest.py --port 13305 --path /api/v1/chat/completions \\
      --api-key lemonade --model <model-name> --label vllm-rocm-fp16 --users 4
      Benchmark vLLM through lemonade (note the different API path and key).
"""

import argparse
import http.client
import json
import os
import re
import statistics
import sys
import threading
import time
from datetime import datetime

# ---------------------------------------------------------------- constants

HOST = "127.0.0.1"          # hardcoded on purpose: localhost only, ever
MAX_USERS = 32              # hard ceiling on simultaneous users
REQUEST_TIMEOUT_S = 120     # max seconds with NO data before a request fails
REQUEST_HARD_CAP_S = 300    # max total seconds per request, even if data flows
MAX_LINE_BYTES = 64 * 1024  # bound on a single stream line (no unbounded buffering)
DEFAULT_MAX_TOKENS = 256    # reply budget per request (same for every engine)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Each request gets a different topic so no two prompts are identical
# (vLLM caches repeated prompt prefixes, which would flatter its numbers).
TOPICS = [
    "how rivers carve canyons over geological time",
    "how honeybees communicate the location of food",
    "how a refrigerator keeps food cold",
    "how earthquakes are measured and compared",
    "how vaccines train the immune system",
    "how glass is made from sand",
    "how migrating birds navigate across continents",
    "how a hydroelectric dam generates electricity",
    "how tides are caused by the moon and sun",
    "how paper is recycled into new paper",
    "how a camera lens focuses light",
    "how yeast makes bread rise",
    "how lightning forms inside a storm cloud",
    "how submarines control their depth",
    "how coral reefs are built by tiny animals",
    "how wind turbines convert wind into power",
    "how the human ear turns vibrations into sound",
    "how cheese is made from milk",
    "how GPS satellites pinpoint a position",
    "how volcanoes form at tectonic plate boundaries",
    "how octopuses change the color of their skin",
    "how a suspension bridge carries its load",
    "how rain forms inside clouds",
    "how batteries store and release energy",
]


def build_prompt(label, users, round_no, user_no):
    """A ~100-token prompt, unique per request (unique first line defeats
    prefix caching), identical in shape/length-class across all requests."""
    topic = TOPICS[(round_no * MAX_USERS + user_no) % len(TOPICS)]
    return (
        f"Request-ID {label}-u{users}-r{round_no}-w{user_no} "
        "(ignore this line; it only makes the request unique).\n"
        f"Please write a detailed, factual explanation of {topic}. "
        "Cover the underlying mechanism step by step, mention two common "
        "misconceptions people have about it, give one concrete real-world "
        "example with approximate numbers, and finish with a short summary "
        "sentence. Keep the tone neutral and informative, suitable for a "
        "curious adult reader without specialist background knowledge."
    )


# --------------------------------------------------- shared-prefix mode
# Real agent/RAG traffic sends a large IDENTICAL system prompt with every
# request and only a small unique user message. Engines with prefix caching
# (vLLM prefix cache, SGLang RadixAttention) only prefill the shared part
# once. --shared-prefix N prepends an identical ~N-token system message to
# every request so that workload shape can be measured; the unique user
# message stays identical to unique-prompt mode, so generation work is the
# same and only the cacheable prefix differs.

SHARED_PARAGRAPH = (
    "You are a careful assistant for a documentation team. Follow these "
    "standing rules for every reply: be factual and neutral; explain "
    "mechanisms step by step; prefer concrete numbers over vague claims; "
    "flag any uncertainty explicitly instead of guessing; avoid marketing "
    "language; use short paragraphs; define any technical term the first "
    "time it appears; when a question cannot be answered from the given "
    "information, say so plainly rather than inventing an answer. "
)


def build_shared_text(approx_tokens):
    """A deterministic system prompt of roughly approx_tokens tokens
    (~4 chars/token), built by repeating a fixed paragraph. Identical for
    every request in a run — by construction, so the server can cache it."""
    reps = max(1, round((approx_tokens * 4) / len(SHARED_PARAGRAPH)))
    return ("Standing instructions (identical for every request in this "
            "benchmark):\n" + SHARED_PARAGRAPH * reps)


def build_messages(args, round_no, user_no):
    """The message list for one request: unique-prompt mode sends a single
    user message; shared-prefix mode prepends the fixed system message."""
    user_msg = {"role": "user",
                "content": build_prompt(args.label, args.users, round_no, user_no)}
    if args.shared_text:
        return [{"role": "system", "content": args.shared_text}, user_msg]
    return [user_msg]


def empty_record():
    return {"ok": False, "complete": False, "error": None, "ttft_s": None,
            "gen_tps": None, "tokens": 0, "tokens_deltas": 0, "total_s": None,
            "server_usage": None}


# ------------------------------------------------------------- one request

def stream_one_request(args, messages):
    """Send one streaming chat request; timestamp every arriving token.
    Returns a result dict. Never raises — failures become results."""
    rec = empty_record()
    body = json.dumps({
        "model": args.model,
        "messages": messages,
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "stream": True,
        # Ask the server to report its own token count in the final chunk.
        # vLLM may pack several tokens into one delta, so counting deltas
        # alone would undercount it; the server's number is authoritative.
        "stream_options": {"include_usage": True},
    })
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = "Bearer " + args.api_key

    conn = http.client.HTTPConnection(HOST, args.port, timeout=REQUEST_TIMEOUT_S)
    t_start = time.perf_counter()
    t_first = None
    t_last = None
    try:
        conn.request("POST", args.path, body, headers)
        resp = conn.getresponse()
        if resp.status != 200:
            rec["error"] = "HTTP %d: %s" % (resp.status, resp.read(300).decode("utf-8", "replace"))
            return rec
        # The reply is a Server-Sent-Events stream: lines of "data: {json}".
        while True:
            if time.perf_counter() - t_start > REQUEST_HARD_CAP_S:
                rec["error"] = "request exceeded hard cap (%ds)" % REQUEST_HARD_CAP_S
                break
            line = resp.readline(MAX_LINE_BYTES)
            if not line:                       # connection closed by server
                break
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                rec["complete"] = True
                break
            try:
                obj = json.loads(payload)
            except ValueError:
                continue                       # tolerate junk lines, count nothing
            # Engines attach their own counters near the end of the stream
            # (vLLM/OpenAI: "usage"; llama.cpp: "timings").
            for key in ("usage", "timings"):
                if obj.get(key):
                    rec["server_usage"] = obj[key]
            choices = obj.get("choices") or []
            if choices:
                if choices[0].get("finish_reason"):
                    rec["complete"] = True     # server says generation finished
                if (choices[0].get("delta") or {}).get("content"):
                    now = time.perf_counter()
                    rec["tokens_deltas"] += 1
                    if t_first is None:
                        t_first = now
                    t_last = now
        t_end = time.perf_counter()
        rec["total_s"] = round(t_end - t_start, 4)
        # Prefer the server's own completion-token count when it sent one.
        usage = rec["server_usage"] or {}
        rec["tokens"] = (usage.get("completion_tokens")      # vLLM / OpenAI style
                         or usage.get("predicted_n")         # llama.cpp "timings"
                         or rec["tokens_deltas"])
        if t_first is not None:
            rec["ttft_s"] = round(t_first - t_start, 4)
            if rec["tokens"] > 1 and t_last > t_first:
                rec["gen_tps"] = round((rec["tokens"] - 1) / (t_last - t_first), 2)
        if t_first is None:
            rec["error"] = rec["error"] or "stream ended with no content"
        elif not rec["complete"]:
            # Got some tokens but never a [DONE]/finish_reason: the server
            # likely died mid-reply. That is a finding, not a success.
            rec["error"] = rec["error"] or "stream ended before completion (server died mid-reply?)"
        else:
            rec["ok"] = True
    except Exception as exc:                   # timeout, refused, reset, ...
        rec["error"] = "%s: %s" % (type(exc).__name__, exc)
        rec["total_s"] = round(time.perf_counter() - t_start, 4)
    finally:
        conn.close()
    return rec


# ------------------------------------------------------------------ rounds

def run_round(args, round_no):
    """One round: all N users fire simultaneously (barrier), wait for all.
    Returns (list of N result dicts — one per user, always — , aggregate)."""
    n = args.users
    barrier = threading.Barrier(n + 1)         # +1 for the main thread
    results = [None] * n
    times = [None] * n                         # (start, end) per worker

    def worker(idx):
        messages = build_messages(args, round_no, idx)
        barrier.wait()                         # everyone starts together
        t0 = time.perf_counter()
        rec = stream_one_request(args, messages)
        t1 = time.perf_counter()
        rec.update({"round": round_no, "user": idx, "users": n,
                    "label": args.label})
        results[idx] = rec
        times[idx] = (t0, t1)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(n)]
    for t in threads:
        t.start()
    barrier.wait()                             # release all workers at once
    # One shared deadline for ALL joins (sequential per-thread timeouts
    # would otherwise stack up to n * timeout for a fully hung round).
    deadline = time.monotonic() + REQUEST_HARD_CAP_S + 30
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    # A worker still running past the deadline is abandoned (daemon thread,
    # cannot block exit) — but it MUST still appear in the report; a hung
    # request at high concurrency is exactly the kind of finding we want.
    for i in range(n):
        if results[i] is None:
            rec = empty_record()
            rec.update({"error": "worker still running at round deadline (request abandoned)",
                        "round": round_no, "user": i, "users": n,
                        "label": args.label})
            results[i] = rec

    # Aggregate throughput = all tokens produced / wall-clock of the round,
    # over workers with known start/end times. Deliberate definition:
    # partial tokens from failed requests count (throughput actually
    # achieved), and a timed-out request stretches the wall clock.
    timed = [(results[i], times[i]) for i in range(n) if times[i]]
    if timed:
        wall = max(tm[1] for _, tm in timed) - min(tm[0] for _, tm in timed)
        total_tokens = sum(r["tokens"] for r, _ in timed)
        agg = round(total_tokens / wall, 2) if wall > 0 else None
    else:
        agg = None
    return list(results), agg


# ----------------------------------------------------------------- summary

def fmt(values):
    """min / median / max of the non-None values, as a short string."""
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    return "%.2f / %.2f / %.2f" % (min(vals), statistics.median(vals), max(vals))


def print_summary(label, users, measured, aggs):
    recs = [r for round_recs in measured for r in round_recs]
    ok = [r for r in recs if r["ok"]]
    failed = [r for r in recs if not r["ok"]]
    print()
    print("=" * 64)
    print("SUMMARY  %s  |  %d simultaneous users" % (label, users))
    print("=" * 64)
    print("requests ok/failed : %d / %d" % (len(ok), len(failed)))
    print("TTFT seconds       : %s   (min/median/max)" % fmt([r["ttft_s"] for r in ok]))
    print("per-user gen tok/s : %s   (min/median/max)" % fmt([r["gen_tps"] for r in ok]))
    print("tokens per reply   : %s   (min/median/max)" % fmt([float(r["tokens"]) for r in ok]))
    aggs_clean = [a for a in aggs if a is not None]
    if aggs_clean:
        print("AGGREGATE tok/s    : %.2f   (mean of %d measured rounds)"
              % (statistics.mean(aggs_clean), len(aggs_clean)))
    if users == 1:
        # Calibration compares "per-user gen tok/s" to known single-stream
        # baselines. The aggregate is NOT comparable at N=1: its wall clock
        # includes TTFT/prefill, so it always reads lower.
        print("note: for calibration use 'per-user gen tok/s', not aggregate")
    for r in failed:
        print("  FAILED round=%s user=%s: %s" % (r["round"], r["user"], r["error"]))
    print("=" * 64)


def write_results(args, all_records, aggs):
    """Append-only: a new timestamped .jsonl file per run, never overwrite."""
    outdir = os.path.join(SCRIPT_DIR, "results")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = "%s_%s_u%d" % (stamp, args.label, args.users)
    f = None
    for suffix in [""] + ["-%d" % i for i in range(2, 10)]:
        path = os.path.join(outdir, base + suffix + ".jsonl")
        try:
            f = open(path, "x")                # "x" = fail rather than overwrite
            break
        except FileExistsError:
            continue                           # same second + same label: rare
    if f is None:
        print("could not create a results file (name collisions) — results not saved")
        return
    with f:
        for rec in all_records:
            f.write(json.dumps(rec) + "\n")
        f.write(json.dumps({"summary": True, "label": args.label,
                            "users": args.users, "aggregate_tps_rounds": aggs,
                            "max_tokens": args.max_tokens,
                            "shared_prefix_tokens": args.shared_prefix}) + "\n")
    print("results written to: %s" % path)


# -------------------------------------------------------------- main runs

def run_benchmark(args):
    # Derived here (not in main) so --selftest gets it too.
    args.shared_text = (build_shared_text(args.shared_prefix)
                        if args.shared_prefix else None)
    print("target http://%s:%d%s | label=%s | users=%d | warmup=1 measured=%d"
          % (HOST, args.port, args.path, args.label, args.users, args.rounds))
    if args.shared_text:
        print("shared-prefix mode: identical ~%d-token system message on every "
              "request (%d chars)" % (args.shared_prefix, len(args.shared_text)))
    t_run_start = time.perf_counter()
    all_records = []
    measured = []
    aggs = []
    try:
        for round_no in range(args.rounds + 1):    # round 0 = warm-up
            if time.perf_counter() - t_run_start > args.max_minutes * 60:
                print("wall-clock cap (%d min) reached — stopping early." % args.max_minutes)
                break
            kind = "warm-up " if round_no == 0 else "measured"
            print("round %d (%s) ... " % (round_no, kind), end="", flush=True)
            recs, agg = run_round(args, round_no)
            n_ok = sum(1 for r in recs if r["ok"])
            print("done: %d/%d ok, aggregate %s tok/s" % (n_ok, args.users, agg))
            errs = [r for r in recs if not r["ok"]]
            if errs:                               # surface problems immediately
                print("   first error: %s" % errs[0]["error"])
            for r in recs:
                r["warmup"] = (round_no == 0)
            all_records.extend(recs)
            if round_no > 0:                       # warm-up is discarded from stats
                measured.append(recs)
                aggs.append(agg)
    except KeyboardInterrupt:
        print("\ninterrupted — reporting what was measured so far.")
    print_summary(args.label, args.users, measured, aggs)
    if all_records:
        write_results(args, all_records, aggs)
    return all_records, measured


# ------------------------------------------------------------- self-test

FAKE_TOKENS = 40        # the fake server streams this many tokens per reply
FAKE_TTFT_S = 0.20      # ... after pretending to "prefill" for this long
FAKE_DELAY_S = 0.005    # ... with this gap between tokens (≈200 tok/s)


def start_fake_server():
    """A tiny fake LLM server (stdlib only) that speaks just enough of the
    OpenAI streaming protocol for the harness to benchmark it."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"          # body ends when we close — simple

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            time.sleep(FAKE_TTFT_S)
            for i in range(FAKE_TOKENS):
                chunk = {"choices": [{"delta": {"content": "tok%d " % i}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                self.wfile.flush()
                time.sleep(FAKE_DELAY_S)
            self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}], '
                             b'"usage": {"completion_tokens": %d}}\n\n' % FAKE_TOKENS)
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *a):             # keep the test output clean
            pass

    srv = http.server.ThreadingHTTPServer((HOST, 0), Handler)  # port 0 = pick a free one
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def selftest():
    print("SELF-TEST: starting fake LLM server on localhost ...")
    srv = start_fake_server()
    # Build args through the real parser so the two can never drift apart.
    args = build_parser().parse_args([
        "--port", str(srv.server_address[1]), "--label", "selftest",
        "--users", "3", "--rounds", "2", "--max-minutes", "5",
    ])
    all_records, measured = run_benchmark(args)
    srv.shutdown()

    # The fake server's behavior is known exactly — so we can assert hard.
    problems = []
    ok = [r for round_recs in measured for r in round_recs if r["ok"]]
    if len(ok) != args.users * args.rounds:
        problems.append("expected %d ok measured requests, got %d"
                        % (args.users * args.rounds, len(ok)))
    for r in ok:
        if r["tokens"] != FAKE_TOKENS:
            problems.append("token count %s != %d" % (r["tokens"], FAKE_TOKENS))
        if r["tokens_deltas"] != FAKE_TOKENS:
            problems.append("delta count %s != %d" % (r["tokens_deltas"], FAKE_TOKENS))
        if not r["complete"]:
            problems.append("request not marked complete despite [DONE]")
        if not (FAKE_TTFT_S * 0.5 < (r["ttft_s"] or 0) < FAKE_TTFT_S * 4):
            problems.append("TTFT %s implausible (fake server uses %.2fs)"
                            % (r["ttft_s"], FAKE_TTFT_S))
        if (r["server_usage"] or {}).get("completion_tokens") != FAKE_TOKENS:
            problems.append("server usage not captured: %s" % r["server_usage"])
    if problems:
        print("SELF-TEST FAILED:")
        for p in problems:
            print("  - " + p)
        return 1
    print("SELF-TEST PASSED — harness measures a known server correctly.")
    return 0


# --------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(description="Concurrency benchmark for local "
                                "OpenAI-compatible LLM servers (localhost only).")
    p.add_argument("--selftest", action="store_true",
                   help="verify the harness against a built-in fake server, then exit")
    p.add_argument("--port", type=int, help="server port on 127.0.0.1 (e.g. 8080)")
    p.add_argument("--path", default="/v1/chat/completions",
                   help="API path (lemonade uses /api/v1/chat/completions)")
    p.add_argument("--api-key", default="", help="bearer token if the server wants one")
    p.add_argument("--model", default="default", help="model name to request")
    p.add_argument("--label", help="name for this configuration, used in filenames")
    p.add_argument("--users", type=int, help="simultaneous users (1..%d)" % MAX_USERS)
    p.add_argument("--rounds", type=int, default=2, help="measured rounds (default 2)")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                   help="reply budget per request (default %d)" % DEFAULT_MAX_TOKENS)
    p.add_argument("--shared-prefix", type=int, default=0,
                   help="prepend an identical ~N-token system message to every "
                        "request (0 = off; measures prefix-caching engines on "
                        "the agent/RAG workload shape)")
    p.add_argument("--max-minutes", type=int, default=30,
                   help="wall-clock cap for the whole run (default 30)")
    return p


def main():
    p = build_parser()
    args = p.parse_args()
    if args.selftest:
        sys.exit(selftest())
    if not (args.port and args.label and args.users):
        p.error("--port, --label and --users are required (or use --selftest)")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.label):
        p.error("--label may only contain letters, digits, dots, dashes, underscores")
    if not (1 <= args.users <= MAX_USERS):
        p.error("--users must be between 1 and %d" % MAX_USERS)
    if not (1 <= args.rounds <= 10):
        p.error("--rounds must be between 1 and 10")
    if not (1 <= args.max_tokens <= 4096):
        p.error("--max-tokens must be between 1 and 4096")
    if not (0 <= args.shared_prefix <= 6000):
        p.error("--shared-prefix must be between 0 and 6000 (must fit the "
                "server's context window alongside prompt + reply)")
    run_benchmark(args)


if __name__ == "__main__":
    main()
