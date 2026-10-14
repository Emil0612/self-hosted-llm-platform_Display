# Gemma-4 26B-A4B on vLLM: serving recipe

```text
Verified against : AMD Strix Halo (gfx1151) node, ~122 GiB system RAM, 100 GiB GTT pool
Engine / build   : vLLM 0.23.1.dev0+rocm7.14.0a20260624 (gfx1151 portable bundle)
Date verified    : 2026-08-12 (latest vLLM-side verification for this stack: the tool-parser
                   gates). The sections below were not all run that day either: the concurrency
                   sweeps, the thinking checks and the KV-cache findings come from several
                   earlier sessions during this build's evaluation.
                   Compare arms within one table, never across two of them.
```

This is the multi-user serving recipe: one vLLM process per GPU node, bound to localhost, reached through
an SSH tunnel or a gateway. For single-stream speed on this hardware, speculative decoding on llama.cpp is
the faster path; see the [hardware overview](README.md).

**The model:** `google/gemma-4-26B-A4B-it`, an MoE with 25.2B total and ~3.8B active parameters, 256K
native context, multimodal, **Apache-2.0 and not gated, so no HF token is required**. Thinking is **off by
default** on Gemma 4 and has to be asked for per request.

---

## 1. Prerequisites

- The **portable gfx1151 vLLM bundle**, from the `lemonade-sdk/vllm-rocm` GitHub releases page. It ships
  as two split halves, `...part01-of-02.tar.gz` and `...part02-of-02.tar.gz`, which are concatenated and
  extracted into a directory of your choice:

  ```bash
  cat <bundle>-part01-of-02.tar.gz <bundle>-part02-of-02.tar.gz | tar xz
  ```

  The release name is the version string in the header above. Plain `pip install vllm` does not support
  gfx1151. **Confirm upstream:** check that page for a newer build before using this one. The ROCm base a
  bundle is built on is what decides whether it works at all, as the next bullet shows.
- **`chmod +x <bundle>/bin/*`**, because extraction strips the exec bit and the first run otherwise gives
  `Permission denied`. That is the exec bit, **not** an ownership or sudo problem; never reach for sudo here.
- Smoke-test before anything else: `<bundle>/bin/vllm-server --help` must print **no** `vllm._C` /
  `vllm._rocm_C` import warnings. An `undefined symbol: _ZN3c103hip19getCurrentHIPStreamEa` warning is
  fatal, not cosmetic: the compiled ops are missing and engine init will die later with
  `AttributeError: '_OpNamespace' '_C' object has no attribute 'silu_and_mul_quant'`. That defect is
  ROCm-7.13.0-wide and affects every bundle built on it; the 7.14.0 base is clean.
- BF16 weights are ~50 GB and download to `~/.cache/huggingface` on first launch.

### Launch via the wrapper, and never trust `ps` for the launch command

`bin/vllm-server` sets `LD_LIBRARY_PATH` to the bundle's own libraries and then **`exec`s into**
`python3 -m vllm.entrypoints.openai.api_server`.

> [!WARNING]
> **Two consequences, both of which can take a serving node down.**
> 1. **The visible process command line is the POST-exec form.** `ps` shows
>    `.../bin/python3 -m vllm.entrypoints.openai.api_server --model …`. Relaunching from that literal line
>    skips the wrapper's environment setup and dies at startup with `RuntimeError: Failed to infer device
>    type`, because vLLM cannot find the ROCm/HIP libraries. **Always relaunch via `bin/vllm-server`.**
>    Check the exec bit (`test -x <bundle>/bin/vllm-server`) *before* killing anything; the bundle can ship
>    without it.
> 2. **`pkill -f vllm-server` matches nothing**, because after the exec the command line no longer contains
>    the string `vllm-server`. The old server survives, keeps its ~80 GiB reservation, and the next launch
>    fails with `ValueError: Free memory on device cuda:0 (17.34/100.0 GiB) … less than desired GPU memory
>    utilization`. Use `pkill -f vllm`.

> [!IMPORTANT]
> **A process command line is authoritative about FLAGS and never about ENVIRONMENT.** Reading flags off
> `ps` is the correct way to find out what a running server was actually given, and it is how an edit
> that was believed applied but never took effect becomes visible. But everything the wrapper does before
> the exec,
> such as library paths and exported variables, is invisible there. Verify flags on the process;
> reconstruct the launch from the script.

---

## 2. Serve

Keep the launch in a script (`~/bin/serve26b.sh`) so the running configuration is one file, and the flags
on `ps` can be checked against it.

```bash
#!/usr/bin/env bash
V=<bundle>/bin/vllm-server
M=google/gemma-4-26B-A4B-it

A="--served-model-name gemma4-26b --host 127.0.0.1 --port <port>"
B="--dtype bfloat16 --max-model-len 16384 --gpu-memory-utilization 0.8"
C="--reasoning-parser gemma4"

exec $V --model $M $A $B $C
```

**Your choice:** `~/bin/serve26b.sh` and `~/vllm_gemma.log` (used in the restart sequence below) are our
paths, not required ones; put both wherever you keep scripts and logs. Whatever you pick, **keep them
stable across restarts**, or a later debugging session tails a log the running server no longer writes to.

| Flag | Why |
|---|---|
| `--dtype bfloat16` | The 4-bit path is not available in a vLLM shape for this MoE; see below |
| `--max-model-len 16384` | 256K native context would balloon KV; cap it to what callers actually send |
| `--gpu-memory-utilization 0.8` | Budget for KV. **Read the section on KV cache before changing it** |
| `--host 127.0.0.1` | vLLM defaults to `0.0.0.0` (all interfaces). Keep the engine localhost-only |
| `--reasoning-parser gemma4` | Keeps the model's scratchpad out of `content` when thinking is enabled |
| *(no `--max-num-seqs`)* | Deliberately absent; see below |

### `--max-num-seqs`: the flag to leave out

Raising an inherited `--max-num-seqs 16` to 64 measured **+37% aggregate throughput and 12× better
time-to-first-token at 32 concurrent users** (equivalently, the cap was costing ~27%). ⚠ The fix this
recipe ships is removing the flag entirely, which yields the engine default 128 — a setting no arm
measured. Measured on one gfx1151 node with the real model, BF16,
256-token replies, zero failed requests throughout:

| Config | Users | Aggregate t/s | Median TTFT | Per-user t/s |
|---|---|---|---|---|
| `--max-num-seqs 16` | 32 | ~122 | **~17.5 s** (max ~35 s) | ~7.8 |
| `--max-num-seqs 64` | 32 | **~168 (+37%)** | **~1.4 s (~12× better)** | ~5.4 |
| `--max-num-seqs 256` + `--max-num-batched-tokens 16384` | 64 | **~221** | ~2.3 s | ~3.6 |

**The cap never had a memory justification:** GPU KV cache usage was 2-3% at 32 users and 13-15.5% at 64,
with `Running: 64, Waiting: 0` at the high setting, so nothing was ever queued for lack of cache.

The engine's own default is **128** (`vllm/config/scheduler.py:44`, `DEFAULT_MAX_NUM_SEQS`, read in this
bundle's source; a web snippet claiming 1024 is wrong). **Deleting the flag is the fix.** Substituting
another hand-picked number reproduces the same failure in a milder form; set an explicit value only if you
want headroom above stock, and write down why. Note the constraint from the same file:
`max_num_batched_tokens` must be at least `max_num_seqs`, which the 8192 default satisfies for any sane
value.

Above ~32 concurrent it becomes a genuine trade rather than a free win: more aggregate throughput, slower
per-user generation, higher TTFT.

### Thinking and the reasoning parser

- **Gemma 4 thinking is off by default.** Reasoning tokens are never generated unless a request asks:
  `"chat_template_kwargs":{"enable_thinking":true}`. This works per-request on this build with no restart.
- **There is no server-wide switch on 0.23.1.** `--default-chat-template-kwargs` does not exist and
  argparse exits 1 instantly. *(An instant exit 1 is a bad flag; a slow death is memory.)*
- **If thinking is ever enabled for real traffic, `--reasoning-parser gemma4` is required, not optional.**
  Without it, `content` begins with a bare `thought` line and the raw scratchpad, so a downstream agent
  node receives the scratchpad *as* the answer. With it, `content` starts at the final answer.
- ⚠ **Accepted cost: the reasoning is stripped and discarded, not returned.** `reasoning_content` stays
  `None` on this build (upstream issue #38855, where `<|channel>` delimiters are stripped before parsing).
  If an audit trail of the model's reasoning matters to the caller, this configuration does not provide one.
- **Cost of thinking, measured here:** a schema-constrained one-line task went from ~218 output tokens
  with thinking off to ~790-900 with it on. Separately, **a third party reports 18-22× inflation on
  decision-style prompts; that is their measurement of their own workload, not ours**, and it is
  quoted only to show how large this can get on a prompt whose answer is short. **Raising
  `max_tokens` is mandatory with thinking on**, or reasoning eats the whole budget and the answer
  never arrives.
- **Determinism:** with `"temperature": 0` sent explicitly, 10 sequential identical calls returned exactly
  215 tokens every time. Omitting the field entirely gives ordinary sampling variance (172-254 tokens on
  the same probe). vLLM is not bitwise deterministic under continuous batching, so concurrent harnesses can
  still diverge where sequential ones do not.

### Optional: `--enable-auto-tool-choice --tool-call-parser gemma4`

Verified working on this bundle, with real `tool_calls` arrays carrying valid JSON arguments and no
delimiter leak, in non-streaming, streaming, and 10-concurrent modes. Without it the model's tool syntax
arrives as prose in `content` and `tool_calls` is never emitted.

> [!WARNING]
> **This is not a purely additive change: it moves markup out of `content` and into `tool_calls`.** A
> client that currently copes by reading `content` breaks the moment the parser lands. Ship it as a
> correctness change with notice, at a restart you need anyway.
>
> **Pre-flight:** confirm `gemma4_tool_parser.py` exists in *that node's* bundle. If it is absent the
> server refuses to start, which is how you take a serving node down.

---

## 3. Restart sequence

> [!WARNING]
> **Do not gate the relaunch on a fixed `sleep`, and do not assume your `pkill` matched.** The way we
> hit `ValueError: Free memory on device cuda:0 (17.34/100.0 GiB)` was **`pkill -f vllm-server`
> matching nothing** — the wrapper `exec`s into `python3`, so the command line no longer contains
> "vllm-server", the old server survived holding tens of GiB, and the next launch died reading like a
> capacity problem when it was a still-running process. A crashed engine child can orphan the same way
> while holding memory. **So gate on both: every `vllm` process gone, and the port free** — an
> orphaned child holds GTT without holding the listening socket, so the port check alone passes
> exactly when this failure is live. The `rocm-smi` line is a reading you have to look at, not a gate;
> if the pool is still held, wait and re-run it rather than relaunching.

One paste, self-sequencing:

```bash
pkill -f vllm
```
```bash
# both gates: processes actually gone, THEN the port actually released
while pgrep -f vllm > /dev/null; do sleep 2; done; echo NO-VLLM
until ! ss -tln | grep -q ":<port> "; do sleep 2; done; echo PORT-FREE
rocm-smi --showmeminfo vram gtt   # READ this — it prints, it does not gate. Pool still held? wait.
```
```bash
cp ~/vllm_gemma.log ~/vllm_gemma.log.bak-$(date +%Y%m%d) 2>/dev/null || true
```
```bash
nohup ~/bin/serve26b.sh > ~/vllm_gemma.log 2>&1 &
```
```bash
until grep -q "Application startup complete" ~/vllm_gemma.log; do sleep 15; done; echo SERVER-UP
```

⚠ Note the trailing space in `":<port> "` — without it the pattern also matches the peer-address column, so a client connection to that port reads as the server still listening.
The startup gate is the engine's own `Application startup complete` line; its first pass can fire
before `nohup`'s redirect has created the log, giving one harmless "No such file" before the loop
self-corrects.

A server running under `nohup` does not survive a reboot. Promoting it to a user-level systemd unit needs
`KillMode=mixed`, an `ExecStopPost` cleanup and a wait-for-free-memory gate, or `Restart=always` with a
short `RestartSec` walks straight into the "old process still holds the memory" loop above.

**Verify on the process, not on the file.** A saved script is not a running configuration:

```bash
ps -eo etime,args | grep "[g]emma-4-26B"
ps -eo args | grep "[g]emma-4-26B" | tr ' ' '\n' | grep -c "reasoning-parser"   # expect 1
```

Uptime that predates your restart, or a flag count of 0, means the edit never took effect.

---

## 4. Measured performance

BF16, short prompts unless stated, one gfx1151 node.

| Condition | Result |
|---|---|
| Single stream (400-token reply) | **~24.5 t/s** |
| Single stream at 26k context depth | **~16 t/s**, so a 3,000-token answer has a ~187 s floor. ⚠ **Measured on a configuration with a larger context window than this recipe ships** — under the `--max-model-len 16384` above, a 26k-token request is rejected with HTTP 400 rather than served slowly. The cell is kept because the *shape* (decode degrades with depth) is what matters when sizing a long-context workload; the depth itself is not reachable as configured |
| 32 concurrent, 256-token replies | **~168 t/s aggregate**, ~1.4 s median TTFT. ⚠ **Measured at `--max-num-seqs 64`**, which the serve script above deliberately does not set |
| 64 concurrent | **~221 t/s aggregate**, ~2.3 s median TTFT. ⚠ **Measured at `--max-num-seqs 256` plus `--max-num-batched-tokens 16384`**, neither of which the serve script sets |

⚠ **Neither row describes the shipped configuration.** As shipped the flag is absent and the engine
default of 128 applies — a point between the two measured arms, and **no throughput arm was run
there**. The finding these rows support is that the inherited cap of 16 was costing throughput, not
that 64 or 256 is the right value; see the section above before adding the flag back.

The single-stream figure sits close to the bandwidth roofline for this model, but **no exact
percentage is quotable, because we have never measured an effective-bandwidth constant for BF16 on
this hardware.** ~4B active parameters in BF16 is about 8 GB/token, and the two constants we did
measure only bracket the prediction: 218 GB/s (4-bit K-quant) gives ~27 t/s and 228 GB/s (`Q8_0`)
gives ~28.5, while the ~230 GB/s unquantized figure in the [folder README](README.md) is an
extrapolation of that trend rather than a measurement. Against 24.5 measured, that is somewhere
around **85-90% of roofline depending on which constant you pick**, and picking one is the part we
cannot yet justify. **vLLM is not leaving speed on the table here; it is being handed 2.7× more
bytes to read than a 4-bit build would be.** The same model at 4-bit measures ~72 t/s on llama.cpp — but that is a `llama-bench` **raw generation** figure, not a
serving number, and it is **not a clean A/B against the 24.5**: different node, different month, with
another job resident during the run. Re-run the pair together before anyone leans on it. The direction
still holds regardless: Google will not ship a 4-bit vLLM checkpoint for this MoE (small expert
dimensions lose too much quality at 4 bits), and the 4-bit they do bless is QAT, which ships only as
GGUF. Choosing vLLM for this model is choosing BF16.

> [!IMPORTANT]
> **Always state the depth a decode rate was measured at.** Quoting ~24.5 t/s for a long-context agentic
> workload overstates it by ~50%.

A separate concurrency sweep with a different prompt and reply shape recorded higher aggregates
(~90 t/s at 8 concurrent, ~145 at 16, ~200 at 32, ~270 at 64, with in-run peaks above that decaying as KV
fills). Treat the two ladders as separate instruments: compare arms within one run, never across.

**Scaling across nodes is linear.** Two nodes each serving 16 concurrent delivered about 2× a single node's
16-concurrent figure, all requests 200 OK. That is replica scaling, not sharding.

---

## 5. KV cache size is a launch condition, not a hardware property

Two nodes of the same class once reported KV caches differing by **5.8×**, on machines we then believed to
be different hardware. They were not, and they were not running the same configuration either: the pair
differed by `--gpu-memory-utilization` (0.85 against 0.8), with different jobs resident at launch.
**Re-running both on the same configuration was the control that dissolved the gap** — which is how we
know the difference was never hardware.

The mechanism is unified memory. vLLM on this APU derives "free GPU memory" from **host free RAM**, so
the KV budget is decided by `--gpu-memory-utilization` *and* by whatever else happened to be resident at
launch time. A resident ~20 GiB
llama.cpp process was enough to make `--gpu-memory-utilization 0.85` fail outright with
`Engine core initialization failed`, where 0.65 coexisted fine.

**Never infer a machine's capacity from a KV number without checking what else was resident.** Conversely,
a KV number that barely moves when `--max-num-seqs` goes from 16 to 128 confirms what that flag is:
`max_num_seqs` is a **ceiling on admitted sequences, not a reservation**, and KV is sized by
`--gpu-memory-utilization` alone.

Before launching anything large:

```bash
rocm-smi --showmeminfo gtt          # watch GTT, not the VRAM percentage
pgrep -af 'vllm|sglang|llama'       # what else is holding the pool
```

---

## 6. Known traps

| Trap | What happens | Fix |
|---|---|---|
| Relaunching from the `ps` command line | `RuntimeError: Failed to infer device type` | Launch via `bin/vllm-server` |
| `pkill -f vllm-server` | Matches nothing; old server keeps the memory; next launch fails on free memory | `pkill -f vllm` |
| Fixed `sleep` between kill and relaunch | Intermittent startup failures | Gate on the port being free, **read** `rocm-smi` to confirm GTT came back (it does not gate itself), then wait for `Application startup complete` |
| Missing exec bit after extraction | `Permission denied` | `chmod +x <bundle>/bin/*`, never sudo |
| Judging GPU fullness by `rocm-smi` VRAM% | Reads a 512 MB carveout, not the pool (28% idle against 29% loaded) | Read GTT, or vLLM's own `GPU KV cache size` / `Free memory` startup lines |
| Trusting an edited launch script | The change may never have been applied | `ps -eo etime,args`, to verify uptime and flags on the process |
| `curl -d @~/file.json` | Bash expands `~` only at the start of a word, and this word starts with `@` | `-d @$HOME/file.json` |
| Small `max_tokens` with thinking enabled | `finish_reason: length`, empty content, because reasoning ate the budget | Derive `max_tokens` from context minus prompt, never a round number |
| Benign startup noise | `Auto-prefetch disabled because EXT4 not a network FS`, long INFO/WARNING walls | Only `error / failed / cannot / CPU fallback / out of memory` matter |

---

## 7. Teardown

```bash
pkill -f vllm
```
```bash
while pgrep -f vllm > /dev/null; do sleep 2; done; rocm-smi --showmeminfo gtt; echo STOPPED
```
```bash
rm -rf <bundle>
```

Confirm the GTT pool actually came back before you conclude the node is free; a process can be gone while
its reservation is not yet released. Then remove `~/bin/serve26b.sh` and the `~/vllm_gemma.log*` files if
you created them, and the BF16 weights under `~/.cache/huggingface` (~50 GB) if nothing else uses them.

Nothing was installed system-wide: no systemd unit, no packages, no sudo at any step.

---

**See also.** The two concurrency ladders above are separate instruments before they are numbers,
and [how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) is where that
distinction is written down. [numbers.md](../../reference/numbers.md) holds the cross-stack table
these aggregates belong to, and
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md) covers when to reach
for a batching stack rather than a single-stream one. Multi-user means keys and quotas, which is
[the gateway runbooks](../gateway/README.md).
