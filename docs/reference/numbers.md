# Numbers: what to expect

Almost everything on this page was measured by us, on our own hardware, with our own harness — the
exceptions are labelled where they appear, and third-party figures are always named as theirs.
Figures are quoted as **ranges with the workload named**. A single number without a workload
attached is not a result. Read [how-to-read-these-numbers.md](how-to-read-these-numbers.md)
before comparing any of this against a figure you found online.

Two hardware classes appear throughout:

- **DGX Spark (GB10)**: Grace Blackwell superchip, 20 Arm cores, 128 GB LPDDR5X unified memory,
  vendor-spec 273 GB/s, `sm_121`.
- **Strix Halo (gfx1151)**: AMD APU, unified memory, **measured** bandwidth 218 GB/s (Q4) /
  228 GB/s (Q8). Per-format, not one number.

---

## 1. The table

**At a glance.** One line per served configuration. The single-stream column **names its own
workload**, because these rows are not all measuring the same thing: some are agentic replay, some
are decode at a named depth, one is fresh generation, one is a plain completion. Everything else
each row measured is in its linked subsection below.

| Class | Model | Stack | Single-stream headline, workload named | Concurrency | Detail |
|---|---|---|---|---|---|
| DGX Spark | Qwen3.8-27B NVFP4 | SGLang + DFlash2 | **~35-41 t/s** agentic replay | **~162 t/s** agg at 8, at ~6.5k, §2 | [1a](#1a-qwen38-27b-nvfp4-on-sglang) |
| DGX Spark | Qwen3.8-27B GGUF | llama.cpp + DFlash2 | **~29-37 t/s** agentic replay | Not measured | [1b](#1b-qwen38-27b-gguf-on-llamacpp) |
| DGX Spark | Gemma-4 26B-A4B q4_0 | llama.cpp + MTP | **~81 t/s** agentic replay, thinking off | Not measured | [1c](#1c-gemma-4-26b-a4b-with-mtp) |
| DGX Spark | Flash-Next 180B GGUF | llama.cpp native | **~22-26 t/s** fresh generation | Not measured | [1d](#1d-flash-next-180b-gguf-on-llamacpp) |
| DGX Spark | Flash-Next 180B NVFP4 | vLLM | **~33-36 t/s** decode, flat 8k→128k | **8 at ~33k**, 5 at ~59k, §6 | [1e](#1e-flash-next-180b-nvfp4-on-vllm) |
| Strix Halo | Flash-Next 180B GGUF | llama.cpp Vulkan | **~24-25 t/s** decode at 8k | Not measured, single slot | [1f](#1f-flash-next-180b-gguf-on-vulkan) |
| Strix Halo | Qwen3.8-27B GGUF | llama.cpp Vulkan + DFlash2 | **~26-28 t/s** agentic replay | 60-77 t/s agg at 4 | [1g](#1g-qwen38-27b-gguf-on-vulkan) |
| Strix Halo | Gemma-4 26B-A4B BF16 | vLLM 0.23.1 | **24.5-24.6 t/s** plain completion | ~168 t/s at 32 users | [1h](#1h-gemma-4-26b-a4b-bf16-on-vllm) |

Each subsection below is **the rest of one row of that table** — the same row, unstacked, one bullet
per workload, plus the full stack description the row was measured on.

### 1a. Qwen3.8-27B NVFP4 on SGLang

**Class:** DGX Spark (GB10). **Model:** Qwen3.8-27B (NVFP4).
**Stack:** SGLang + DFlash2, draft budget 8.

- **~35-41 t/s** agentic replay, thinking on at default (xhigh) effort
- **~48-49 t/s** same replay at low effort
- **~42-50 t/s** 512-token pure-code
- **~25 t/s** 512-token prose
- **Time to first token (TTFT) 0.14-0.41 s on every turn**
- Concurrency: **Measured.** 1 / 4 / 8 sessions → ~35.6 / ~33 / ~25 t/s per stream; ~162 t/s aggregate at 8. See §2

### 1b. Qwen3.8-27B GGUF on llama.cpp

**Class:** DGX Spark (GB10). **Model:** Qwen3.8-27B (GGUF Q4_K_XL).
**Stack:** llama.cpp + DFlash2, `--spec-draft-n-max 8`.

- **~29-37 t/s** agentic replay (pooled ~33)
- **~41.5 t/s** maximally-draftable pure code, 64.6% acceptance
- **12.18 t/s** bare decode, no speculation (`llama-bench tg128`)
- TTFT ~4.2 s on a big-prefill turn
- Concurrency: **Not measured**

### 1c. Gemma-4 26B-A4B with MTP

**Class:** DGX Spark (GB10). **Model:** Gemma-4 26B-A4B (Google QAT `q4_0`).
**Stack:** llama.cpp + MTP drafter, `n-max 3`.

- **~81 t/s** agentic replay thinking off
- **~87 t/s** same replay thinking on
- **86-101 t/s** decode on a harder agentic task
- **59.5 t/s** bare decode, no speculation
- MTP multiplier **1.63× on a code smoke** (96.7 t/s against 59.5 bare), acceptance 68.1%. ⚠ That is
  the code cell, not the replay cells above — do not apply it to them
- Concurrency: **Not measured.** No `-np` concurrency ladder has been run on this stack

### 1d. Flash-Next 180B GGUF on llama.cpp

**Class:** DGX Spark (GB10). **Model:** Qwen3.8-Flash-Next 180B (UD-Q4_K_XL, 4 shards).
**Stack:** llama.cpp native, `ngram-mod` spec, n-gram embedding table pinned to CPU/NVMe.

- **~22-26 t/s** on fresh generation (code 23.0, prose 23.0, agentic replay pooled 25.8)
- **~82 t/s** on file-edit / rewrite work, warm
- Concurrency: **Not measured**

### 1e. Flash-Next 180B NVFP4 on vLLM

**Class:** DGX Spark (GB10). **Model:** Qwen3.8-Flash-Next 180B (NVFP4, 206 shards).
**Stack:** vLLM, patched image, PLE table mmap'd from local NVMe, MTP=2.

- **~33-36 t/s** decode and **flat from 8k to 128k context** (~28 t/s at 248k)
- prefill **~2,100 t/s, linear in depth**
- **~22 t/s** wall-inclusive on agentic replay
- retrieval **verified correct at 247,899 tokens**
- Concurrency: **Measured at four depths, and the answer depends on depth.** At ~6.5k it saturates at four (1 / 4 / 8 sessions → ~22 / ~42 / ~42 t/s aggregate full-window). At **~33k, eight sessions give 31-34 t/s aggregate, ~4 t/s each** and still beat four by ~22%. Past ~35k the pool binds: **6-7 admitted at ~45k, 5 at ~59k**, aggregate ~11-12 t/s. Measured pool **514,149 tokens**; correctness **8/8 on a separate single-turn probe at ~33k**, which admitted seven of eight clients. ⚠ One client prefilling 128k costs the others **over half their rate, and the worst-hit ~80%**. See §6

### 1f. Flash-Next 180B GGUF on Vulkan

**Class:** Strix Halo (gfx1151). **Model:** Qwen3.8-Flash-Next 180B (GGUF, 4.27bpw Q4_K_M).
**Stack:** stock llama.cpp Vulkan, build 10858, n-gram table paged from local NVMe.

- **~24-25 t/s** decode at 8k, falling to **~12 t/s at 128k**
- prefill **~400-420 t/s** at 8k, **~170 t/s** at 128k
- **~24 t/s** on agentic replay
- retrieval **verified correct at 8k / 32k / 64k / 128k**
- Concurrency: **Not measured — single slot.** A second request queues behind the first, and behind a 128k prefill that is a ~12-minute stall

### 1g. Qwen3.8-27B GGUF on Vulkan

**Class:** Strix Halo (gfx1151). **Model:** Qwen3.8-27B (GGUF Q4_K_XL).
**Stack:** llama.cpp Vulkan + DFlash2, **`--spec-draft-n-max 4` as the recipe ships** (`8` only on a
server dedicated to code generation, where it is worth ~+23%).

- **~26-28 t/s** agentic replay and real agent loop
- **~50-51 t/s** on a *maximally-draftable* pure-code prompt **at `--spec-draft-n-max 8`**, not at the shipped depth 4, which measures ~41 t/s on the same prompt (a ceiling either way, see the warning below)
- **12.15 t/s** bare decode, no speculation (`llama-bench tg128`, not a served figure)
- ITL 19.6 ms at n-max 8 vs 24.2 ms at n-max 4 vs ~82 ms unspeculated
- Concurrency: **Partially measured.** 4 concurrent, 512-token pure code: **60-77 t/s aggregate** depending on draft config. Nothing beyond 4 measured on this stack

### 1h. Gemma-4 26B-A4B BF16 on vLLM

**Class:** Strix Halo (gfx1151). **Model:** Gemma-4 26B-A4B (BF16).
**Stack:** vLLM 0.23.1 (barebones ROCm bundle).

- **24.5-24.6 t/s** on a 400-token plain completion, which is roughly **85-90% of the BF16 bandwidth roofline** — no exact figure, because **no bandwidth constant has been measured for BF16 on this hardware**; the measured 218 (Q4) and 228 (Q8) constants only bracket it. See the [runbook](../runbooks/strix-halo/gemma4-26b-vllm-serving.md)
- ⚠ **That figure is depth-free and does not survive being quoted at depth.** The same stack measures **~16 t/s at 26k context**, so quoting 24.5 t/s for a long-context agentic workload overstates it by roughly 50%. Always state the depth a decode rate was measured at. ⚠ **That 26k cell came from a configuration with a larger context window than the recipe ships** (`--max-model-len 16384`), so it describes the shape of the depth penalty rather than something this served configuration will accept
- Concurrency: **Measured, two sweeps.** 256-token replies: 8 → ~90, 16 → ~145, 32 → ~200 (peak ~270), 64 → ~270 (peak 339, settling ~250) t/s aggregate. A `--max-num-seqs` sweep at 32 users: 122 t/s at `--max-num-seqs 16`, **168 t/s at 64**, 221 t/s at 64 users / `--max-num-seqs 256`. ⚠ **No throughput arm was run at the engine default 128, which is what the recipe actually ships** (it omits the flag); the sweep establishes that the inherited cap of 16 was costing ~37%, not that 64 is optimal

Additional cells worth knowing, same sources:

| Hardware class | Model | Stack | Result |
|---|---|---|---|
| Strix Halo (gfx1151) | Gemma-4 26B-A4B (Google QAT `q4_0`) | llama.cpp Vulkan | `llama-bench tg128` **~72 t/s** (72.16 ± 0.04 across runs), `pp512` **~1,400 t/s**, about 100% of the Q4 roofline. ⚠ Not a serving number, and **not a clean A/B against the BF16 row above**: the two figures come from different measurement sessions on different nodes, so they do not support a quant comparison. Only a same-day, same-node pair would |
| Strix Halo (gfx1151) | Gemma-4 31B (dense, BF16) | vLLM 0.23.1 | **~3.5 t/s** single-stream (at the roofline); batches to ~60 t/s aggregate at 32 concurrent |
| Strix Halo (gfx1151) | gpt-oss-20b (MXFP4 GGUF) | llama.cpp Vulkan | **75.80 t/s** single-stream, 512 tokens, apparatus-matched against vLLM's 12.96 on the same box the same day |
| DGX Spark (GB10) | gpt-oss-20b (MXFP4 GGUF) | llama.cpp CUDA | `llama-bench`: **tg32 86.63, tg128 87.09** (two different benchmarks, not two records of one cell), pp2048 **4757.52**. Strix Halo comparison (2026-08-31): tg128 **81.11 on a stock build** / 81.42 on the rejected unmerged fork build, with pp2048 2066.97 also coming from that fork build ⇒ decode +7%, prefill 2.3×. |

- ⚠ **On the DGX Spark gpt-oss-20b row:** The decode ratio is unaffected by which build the AMD
  figure comes from, but **the prefill cell is not a stock-build number**

⚠ **The pure-code cells are ceilings, not planning figures.** The ~50 t/s Strix Halo number comes
from a prompt written to be maximally draftable ("output only Python code, no prose, no comments"),
thinking off, single turn, no tools. On a real agent task every configuration we tested on that box
delivered **~26-28 t/s**, the same band as the agentic-replay cell in the row above. Quoting the
ceiling as the planning number oversells real throughput by roughly 2×.

⚠ **The code-generation figure on this Strix Halo Qwen stack spans roughly 30-51 t/s across our
pages, and all of those cells are real.** The [recipe table](../runbooks/strix-halo/README.md)
quotes ~30-33 t/s, the
[Vulkan runbook](../runbooks/strix-halo/qwen38-27b-llamacpp-vulkan-dflash2.md) tuning box ~41 t/s
at the shipped draft depth, and the Strix Halo Qwen breakout ([§1g](#1g-qwen38-27b-gguf-on-vulkan))
quotes ~50-51 t/s on a maximally draftable prompt **at `--spec-draft-n-max 8`**. Three things
separate them, and the largest is a flag you set: **draft depth** — the runbook's own matched sweep
puts pure code at ~41 t/s on `n-max 4` against ~51 t/s on `n-max 8`, **+23%**; the code prompt used
(draftability is the dominant variable among prompts, see
[how-to-read-these-numbers.md §2](how-to-read-these-numbers.md#2-the-hidden-variable-speculative-decode-acceptance));
plus a **measured apparatus offset of roughly +11-13% from enabling the chat template and
explicitly disabling thinking**. Always say which prompt and which harness a code-generation cell
came from.

⚠ **The two bare-decode cells at ~12.1-12.2 t/s are a tie, not a percentage.** The DGX Spark 12.18
and the Strix Halo 12.15 land on top of each other, and that is as far as the pair goes: the two
machines' model files were later found to be **different revisions of the same quant**
(17,559,178,144 B against 17,923,394,624 B under the same repository and filename), so **no
percentage may be read off that comparison**. Quote it as "both land at about 12.1-12.2". The `wc -c`
check that catches this is in
[adding-another-node.md](../runbooks/dgx-spark/adding-another-node.md).

⚠ **Cross-model rows are capability statements, not controlled A/B tests.** Gemma-4 26B at ~81 t/s
and Qwen3.8-27B at ~35-41 t/s are different models with different quality profiles measured on the
same replay; the faster one is not the better one.

---

## 2. Concurrency: the measured ladder

Measured on the DGX Spark flagship stack (SGLang + Qwen3.8-27B NVFP4 + DFlash2), shipped
configuration, no restart and no config edit. The instrument runs **N closed-loop sessions of the
same 5-turn agentic transcript**, where turn *k+1* is only sent once turn *k* completes. Each
session gets a unique ~250-token preamble so the sessions do **not** share prefix cache. Tokens
come from the server's own `usage` field, never from counting stream chunks (speculative-decode
chunks are multi-token).

| In flight | Per-stream decode | Aggregate, steady-state | Aggregate, full-window | TTFT turn 1 / later turns |
|---|---|---|---|---|
| 1 session (n=3) | **~35.6 t/s** (32.5 / 35.8 / 35.6) | 34 | 34 | 0.42 s / 1.2 s |
| 4 sessions (n=1) | ~33.1 t/s | 98 | 86 | 2.15 s / 1.37 s |
| 8 sessions (n=3) | **~24.9 t/s** (26.6 / 24.7 / 24.9) | **~162** (188 / 134 / 162) | ~115-125 | 3.9 s / 1.6 s |

⚠ **Single-stream figures across this repo span roughly 33-41 t/s** depending on the day and the
generation mix: this ladder's c=1 cell (~35.6), the agentic-replay band in §1 (~35-41), and the
short-prompt battery's 34.2 against the replay's 40.8 on the same server are all one instrument
inside its own width, not a set of conflicting results. Read
[how-to-read-these-numbers.md §3](how-to-read-these-numbers.md#3-our-10-day-and-composition-band)
before treating any two of them as a difference.

**Headline: at ~6.5k context, one node serves 8 concurrent agentic sessions at ~25 t/s each,
~162 t/s aggregate, holding about 70% of single-stream speed under 8× load.** ⚠ The depth qualifier
is part of the headline, not a footnote — see the ★ box in [§3](#3-context-and-capacity), and
[§6](#6-long-context-the-depth-curve-and-what-sharing-it-costs) for what depth does to the
comparable figures on the other stack. Scaling efficiency is **57%**: ~162 t/s
aggregate at 8 sessions against the **~35.6 t/s per-stream decode rate at one session**, i.e. 4.56×
throughput for 8× concurrency. Taken against the **c=1 aggregate (34)** instead, the same pair
reads 4.76× = 60%. The 57% quoted here is the per-stream-decode base; say which base you are using.
Zero errors across 88 sessions, zero cache retractions, zero throttle (clocks held 2385-2418 MHz
across 480 samples with no throttle event flagged on any of them).

### Cross-instrument validation: a second, unrelated harness lands on the same aggregate

A completely different harness (8 short prompts, 256-token caps, temperature 0, non-streaming)
measured **161.7 t/s aggregate median at 8 concurrent** and the same **57% scaling efficiency** on
the same server. Two unrelated workload shapes landing on the same aggregate is the strongest
evidence we have that these figures are real rather than instrument artifacts.

### Why two aggregate numbers

Both are reported because they answer different questions and neither alone is honest:

- **Steady-state aggregate.** Measured over the window from the first request to the moment the
  *first* session finishes, i.e. the window in which all N sessions are genuinely in flight. This is
  the box's throughput when it is actually N-wide loaded. At 8 sessions: **~162 t/s**.
- **Full-window aggregate.** First request start to last turn end, including the tail where
  finished sessions have drained and the box is running at less than N-wide. This is what a batch
  of N tasks actually costs you end to end. At 8 sessions: **~115-125 t/s**.

The steady-state figure is the capacity number; the full-window figure is the scheduling number.
Quoting only the first overstates a batch job; quoting only the second understates the hardware.

Note also that per-stream *decode* rate (35.6 at c=1) is higher than the c=1 aggregate (34), because
the aggregate divides by wall-clock including time-to-first-token and inter-turn overhead.

### ⚠ Scope of the ladder

- **Measured at roughly 6.5k tokens of context.** See §3; this is the single biggest caveat.
- **12 and 16 concurrent were NOT measured.** Raising the concurrency cap requires a server
  restart, which may resize the memory pools, so that ladder must be re-run in full under the new
  boot and never spliced onto these rows.
- `n=3` per cell is not an uptime claim.

---

## 3. Context and capacity

**Measured KV pool: 196,761 tokens** on the DGX Spark flagship stack at its shipped memory fraction.
This figure was read directly off the running server, from its own reported `max_total_num_tokens`.
It is a measurement, not a calculation, and the table below follows from it.

⚠ **The pool size stands on its own; the per-token derivation behind it does not.** Separately from
that measurement, a derivation from the model's hybrid attention (only 16 of its 64 layers grow KV
with length, giving ~32 KiB/token) also reproduces the pool figure, and the dtype it assumed **is**
recorded: **that derivation assumes FP8 KV, while this stack serves bf16 KV**, because fp8 KV
together with the DFlash2 drafter produces infinite repetition and must not be used here. Those two
facts have never been reconciled. So quote the 196,761-token pool freely, but do not reuse the
~32 KiB/token arithmetic without re-deriving it against a running server at the KV dtype that
server is actually using.

| Per-request context | Max concurrent requests (measured pool, zero cache reuse) |
|---|---|
| 8k | 24 by pool, but the concurrency cap of 8 binds first |
| 32k | **~6** |
| 64k | **~3** |
| 128k | **1** (a single request takes ⅔ of the pool) |
| Largest single request | **~196k tokens; the pool is the ceiling, not the configured 262k context length** |

### ★ Read this before quoting the 8-session result

The concurrency ladder in §2 was run at **~6.5k context**, which sits comfortably inside the pool.
**Repo-sized agent work runs at 20-30k+ context, and there the pool binds first.** ⚠ **The table above
is a ceiling, not a plan**: it divides the pool by the context with no allowance for the occupancy
overhead measured at 1.6-2.2× naive. Applying the sizing rule below — pool ÷ (2 × context) — gives
**about 3 concurrent at 32k** rather than 6. ⚠ **That halving is borrowed, not measured here.** The
1.6-2.2× occupancy overhead was measured on the *long-context* stack, where the factor proved
workload-shaped with no confirmed mechanism — what transfers is the method of reading your own
pool, not the constant. **No measurement of this stack at 20-30k exists at all.** So treat ~6 as the
arithmetic ceiling, plan below it, and confirm against your own server before committing a fleet size.
Never
quote "8 concurrent" next to a repo-worker claim without this paragraph attached.

### Overflow behaviour

- A request over the per-request limit → clean HTTP 400.
- Pool full during decode → **retraction**: the request silently stalls and later re-prefills from
  scratch. Latency spike, no error surfaced. Ops monitoring should treat health-check timeouts as a
  restart trigger, not as a transient.
- Different projects with different system prompts share nothing and evict each other's cached
  prefixes; agents on the *same* project sharing a system prompt see high cache hit rates.

### ★ Working this out for your own stack

**Yours will differ**, because a pool is whatever memory is left after the weights — it follows from
the memory fraction, the checkpoint and the box. The method transfers; the constant does not.

**Do not confuse the two numbers.** `max_model_len` (or the equivalent context flag) is a rule about
**one request**: the longest conversation the server will accept. The **KV pool** is a memory budget
shared by everyone on the box. They are unrelated and either can be the larger — on the flagship
stack an advertised 262,144 was really a 196,761-token pool, so one request could not reach the
advertised length. **A configuration whose pool is smaller than its advertised context length is
publishing a number it cannot serve.**

**Read your own pool from the server's own startup log, never from configuration.** The command per
engine, and the `grep -o` rule that keeps your API key out of the output, are in the runbooks:
[vLLM](../runbooks/dgx-spark/qwen38-flash-next-180b-vllm-nvfp4.md#8-what-a-healthy-node-looks-like) ·
[SGLang](../runbooks/dgx-spark/qwen38-27b-sglang-nvfp4-dflash2.md#8-measured-numbers).

**Then size it:**

> **concurrent users ≈ pool ÷ (2 × context per user)**, capped by the engine's own concurrency limit

On the **long-context stack**, whose measured pool is **514,149 tokens**
([§6](#6-long-context-the-depth-curve-and-what-sharing-it-costs) — not the 196,761 figure above, which
is the flagship stack's), that predicted 8 / 6 / 4 concurrent at ~33k / ~45k / ~59k against
8 / 6-7 / 5 measured — conservative
by zero to one user at every depth. **The 2× is deliberately pessimistic**: measured occupancy ran
1.6-2.2× the naive figure depending on traffic shape, with no confirmed mechanism, so treat it as a
safety margin and confirm against your own server once real traffic is on it. ⚠ That margin came from
multi-turn traffic; single-turn traffic at uniform depth sat at the worse end.

---

## 4. Reasoning effort: an agent-loop lever, not a per-request one

The Qwen3.8 family defaults to its highest reasoning effort. Dropping to `low` via a per-request
`chat_template_kwargs: {"reasoning_effort": "low"}` (no server change; the highest setting stays
available per request) is the single largest wall-clock lever we have found on the flagship stack.

| Measurement | Workload | Effect of low effort |
|---|---|---|
| Single-stream replay, light / single-pass work, 2026-09-02 | 5-turn agentic replay | **−72% tokens** (~1,220 vs ~4,350 per run) ⇒ ~4.2× less decode wall (≈25 s vs ≈107 s for the task) |
| 8 concurrent, 24 sessions per arm, 2026-09-07 | 5-turn agentic replay | **−55% tokens** (1,779 vs 3,934 per session), wall 20.0 vs 33.0 s per session ⇒ **~1.65× more work per hour** |
| Single hard question, one shot (n=1) | one-shot Q&A | **~11%** (783 vs 877 tokens) |

⚠ **On hard, free-running tasks the wall-clock saving largely disappears.** There the clock is set by
how many fix cycles the agent runs, not by how long it thinks inside each one, so fewer thinking
tokens buys nothing if it costs another loop iteration. Across our standing battery low effort netted
**−13% overall (32.1 vs 36.8 minutes)**: roughly **zero on the hardest task** and **25-50% on the
easier ones**. The ~4.2× row above is the light/single-pass ceiling, not a general figure.

**Frame it as an agent-loop lever.** The saving comes from many turns of reasoning being shortened
across a loop; it does **not** halve an individual request. Low effort does not make every call
faster.

⚠ **The gain under load is ~1.65×, and slot-occupancy arithmetic will tell you 3×. Do not use it.**
Reasoning from single-stream token counts — "low effort emits a third of the tokens, so a slot turns
over three times as often" — does not survive batching: at 8 concurrent the box is already
throughput-saturated (full-window aggregate 116 vs 105 across the two arms of the 2026-09-07 run,
which is a separate measurement from the §2 ladder), so cutting tokens per task no longer buys a
proportional slot saving. Low effort is still the right default for worker agents. **~1.65× is the
number that survives measurement.**

**Quality gates passed.** Low effort scored 72/72 on the standing hard-task battery, a tie with
xhigh's own 72/72 on the same nine runs (144/144 across both arms), 6/6 on a free-running
agent coding task including self-recovery from a bad interpreter invocation, and 9/9 on an
adversarial dependency-resolver probe scored against held-back acceptance tests the agent never saw,
including the trap case. These are screens, not certifications.

### ⚠ Every figure above is the 27B flagship stack. It does not transfer to Flash-Next.

> [!WARNING]
> **On the Flash-Next long-context stack, `reasoning_effort: low` produced no saving — in two
> attempts it produced *more* tokens, not fewer.** Both answers were correct. `medium` was never
> tested. **Do not describe low effort as a speed lever on that model.** The −72% and −55% rows above
> were measured on the **27B**, on **multi-turn agentic replay**, and both of those conditions are
> load-bearing.
>
> ⚠ **n=2, and we are not quoting the token counts.** The direction is what we are confident in; the
> magnitude is not established, and needs repeats per arm before it is quoted with numbers attached.

Two facts about that model's controls, read out of the `chat_template.jinja` shipped in its
checkpoint. **These are a code reading, not a behavioural test:**

- **The default is `xhigh`, the maximum** (`reasoning_effort|default('xhigh')`). Every benchmark we
  have ever run against it therefore ran at maximum thinking.
- **The valid values are `xhigh`, `medium` and `low`. There is no `high`.**

⚠ **What an *unrecognised* value does is untested.** Jinja's `default()` filter fires when a variable is
**undefined**, not when it holds an unexpected string — so the fallback covers the *omitted* case
only. A value like `"high"` reaches the template's own branching, and what happens there we have not
established. Treat an unrecognised value as undefined behaviour and pass one of the three.

`enable_thinking: false` is a separate switch and is **uncharacterised** on that model: one run
returned 22 tokens in 1.1 s and the very next identical run produced a full visible derivation. It
removes the hidden reasoning block, but the model may simply reason in the answer instead. n=2 and
contradictory — **it needs a proper harness run before its behaviour can be described.**

---

## 5. What we have **not** measured

Three honest lines, stated so nobody quotes past them:

1. **Quality of the 4-bit NVFP4 checkpoint has only been screened by task completion.** A free-run
   agent finished its coding task 5/5 on that checkpoint. That is a screen, not a certification.
   **No head-to-head comparison against the alternative 4-bit quant (GGUF Q4_K_XL) exists**: no
   perplexity comparison, no KL divergence, no shared eval battery. All we can say is that nothing
   observable broke.
2. **Deep-context concurrency is measured on the long-context stack and unmeasured on the flagship.**
   On the long-context stack a ladder was run at ~33k, ~45k and ~59k ([§6](#6-long-context-the-depth-curve-and-what-sharing-it-costs)),
   so the pool, admission throttling and correctness under load are measured there. **On the
   flagship 27B stack the ladder is still only at ~6.5k, and the [§3](#3-context-and-capacity) table
   remains arithmetic from a measured pool size rather than a measured ladder.** Do not carry one
   stack's depth behaviour across to the other: they use different engines, and their overflow
   behaviour differs (retraction against admission throttling).
3. **There is no soak or long-run stability evidence on the flagship stack.** The longest continuous
   inference loads we have characterised on this hardware class are **12m43s at bring-up** and the
   **40-minute, 480-sample concurrency ladder** of §2 (thermals clean in both: clocks flat, no
   throttle event, plateau 70-73 °C). A **second unit** was cleared the same way on 2026-09-14 — ten
   minutes of continuous 8-way load, clocks 2392-2398 MHz against a 3003 ceiling, no throttle event
   on any of 93 loaded samples, peaking at 64 °C and still climbing when the run ended — which makes
   the check reproducible across units but adds no duration. Separately, the long-context stack sat
   up **16 h 31 m with the model resident** before its first measurement with no drift — that is
   uptime with the model loaded, not 16 hours of load. **None of these is a soak or an uptime guarantee.** Cells of
   `n=3` are not an uptime claim either, and the box's full 240 W power envelope has never been
   exercised, because bandwidth-bound decode never demands it.

---

## 6. Long context: the depth curve, and what sharing it costs

Measured on the DGX Spark long-context stack (vLLM + Qwen3.8-Flash-Next 180B NVFP4, PLE table
mmap'd from local NVMe): the depth curve 2026-09-08, the capacity and deep-concurrency work
2026-09-09. ⚠ **Every figure in this section was taken on a single unit.** A second unit of this
class has since been built and thermally cleared (§5), but none of these measurements has been
repeated on it, so unit-to-unit variation is unknown. Full recipe:
[qwen38-flash-next-180b-vllm-nvfp4.md](../runbooks/dgx-spark/qwen38-flash-next-180b-vllm-nvfp4.md).

> [!WARNING]
> **This stack withholds streamed output and releases it in a fixed ~19-second tail**, measured
> independent of how long generation actually ran (18.6 s at 32k, 18.5 s at 64k, 18.6 s at 128k,
> 19.0 s at 248k). **Every stream-timestamp metric on it is wrong, in the optimistic direction** —
> our own harness reported "95 t/s" and "431 t/s" for a box whose real rate is ~35. All figures
> below come from wall-clock differencing (one run capped tiny = prefill alone, one run long;
> `decode = Δtokens / Δwall`), with token counts from the server's `usage` field.

### The depth curve, single stream

Instrument: a needle planted at **50% depth** in randomised filler, with **independent random filler
per request** so no two requests share a growing prefix. Retrieval is verified rather than assumed —
a wrong answer here arrives as an HTTP 200.

| Context | Prefill | Decode | Retrieval at 50% depth |
|---|---|---|---|
| 8k | ~1,900 t/s | **33-36 t/s** | correct |
| 32k | ~2,155 t/s | **~35 t/s** | correct |
| 64k | ~2,217 t/s | **~36 t/s** | correct |
| 128k | ~2,124 t/s | **~36 t/s** | correct |
| 248k | ~1,972 t/s | **~28 t/s** | correct |

⚠ **Every rung in that table is a single run** (the 8k row is n=2). Only the 8k decode cell is quoted
as a range for that reason; the rest are point values from one measurement each, and should be read
as "about this" rather than as figures with a spread behind them.

**Decode does not degrade with context until well past 128k**, and prefill is a flat rate, so its
cost is linear rather than quadratic. The **mechanism**, offered as an explanation rather than as one of
our measurements: **only 12 of this model's 48 layers grow KV**, giving **~24 KiB per token**.
⚠ **That comes from third-party architecture research, not from our own reading of the checkpoint** —
it is checkable in the model's `config.json` by anyone who wants to, and we have not done so. It would be both why depth is
cheap and why decode finally slows at 248k — the KV read per decoded token reaching ~5.9 GiB there
and overtaking the active-weight read.

⚠ **Two limits on that arithmetic.** The per-token figure is third-party arithmetic over the
publisher's config, not our measurement, and **the depth curve above is what we actually measured**.
And 24 KiB is a **floor, not a total**: it counts the main attention K/V only, while this
architecture's sparse-attention indexer carries its own smaller key cache that no source we found
quantifies. Do not use 24 KiB/token to compute a hard capacity limit.

**Largest request accepted and answered correctly: 247,899 tokens**, against a configured 262,144.
Contrast §3, where the flagship 27B stack's advertised 262,144 was really a 196,761-token pool.

⚠ **That is the largest request we sent, not a ceiling we found.** The probe targeted ~250k, landed
at 247,899 from a chars-per-token estimate, and we never went higher. Do not read it as the model's
limit.

⚠ **And the configured length is never fully available to the prompt.** Prompt and generation share
one window, so a request reserving N output tokens can carry at most roughly (262,144 − N) of prompt.
The usable ceiling is therefore **a function of the caller's `max_tokens`**, and any figure quoted for
it should name the budget it was measured at.

⚠ The 32k-248k decode cells used **maximally draftable output**, so they are a ceiling; the 8k cells
used natural answers and landed in the same band. And retrieval of a planted fact is the *easiest*
long-context task — it is not evidence the model reasons well across 248k.

**What a user feels:** ~60 s before the first token at 128k; ~171 s end to end at 248k.

### Asking a second question about the same document is ~10x cheaper

That cost above is the cost of the **first** question. This stack runs with prefix caching on, so a
follow-up question that keeps the same document in front of it skips the prefill it already did.

Measured through a gateway in front of the stack: an identical ~22,000-token document (90,000
characters), two questions differing only in the final line, thinking disabled so the time is almost
entirely prefill.

| pass | time |
|---|---|
| cold — first question about the document | **14.5 s** |
| warm — new question, same document, same node | **1.3 s** |
| server-reported prefix cache hit rate afterwards | 53.8% |

⚠ **One cold pass and one warm pass — n=1 each.** Quote it as *roughly an order of magnitude on a
single measured pair*, never as a precise multiple. A later set of four warm requests on the same
document spanned **1.28-1.56 s**, which is the better warm range to carry forward.

⇒ **"The first question about a big document is slow, every question after it is fast" is the correct
description of this stack.** It is the single most useful thing to tell someone before they use it,
because the first timing they experience is the worst one they will ever see.

⚠ **Prefix-cache behaviour does not transfer between stacks** — it is a property of the engine and its
configuration, not of the model family. This stack runs with `PREFIX_CACHE=1`; check the flag before
assuming either way.

**What this costs when more than one node serves the same model name.** The cache lives on whichever
node answered the previous turn, so a router that scatters a conversation can land a follow-up on a
cold node. Four identical requests through a two-node gateway, after both nodes had already served
the document, returned in **1.28 / 1.42 / 1.43 / 1.56 s** — no slow run at all.

⚠ **That shows no request paid a cold prefill; it does not show that both nodes answered**, because
per-backend counts were not taken. On the reading that fits it — the cache is per node and persists —
**scattering costs at most one extra cold prefill per node rather than one per turn**. ⚠ The bound
scales with node count, cache blocks are evicted under memory pressure, and all of it was measured on
quiet nodes; it also measures the *speed* of reuse, never its *correctness*, which **Correctness under
load** below addresses.

### The same model on the other hardware class, and the gap widens with depth

The identical model, measured on a Strix Halo (gfx1151) node with stock llama.cpp Vulkan against
the DGX Spark (GB10) figures above. Different engine, quant and KV dtype, so read the **ratio at a
named depth**, never a single headline multiple. The two columns also come from **different runs on
different boxes**, and our replay instrument carries the ±10% day-and-composition band of
[how-to-read-these-numbers.md §3](how-to-read-these-numbers.md#3-our-10-day-and-composition-band),
so treat ratios near 1 as indistinguishable and only the large multiples as real.

| context | Strix prefill | Spark prefill | ratio | Strix decode | Spark decode | ratio |
|---|---|---|---|---|---|---|
| 8k | ~420 t/s | ~1,900 t/s | **4.5×** | ~24.6 t/s | 33-36 t/s | **1.4×** |
| 32k | ~360 | 2,155 | **6.0×** | ~21 | ~35 | **1.7×** |
| 64k | ~273 | 2,217 | **8.1×** | ~16 | ~36 | **2.2×** |
| 128k | ~170 | 2,124 | **12.5×** | ~12 | ~36 | **3.1×** |

**On the GB10 box both rates are essentially flat with context. On gfx1151 both decay** — decode by
about 52% between 8k and 128k. In practice: a 128k document costs **~12 minutes** to first token on
the AMD node and **~60 seconds** on the NVIDIA one.

⚠ **Do not turn this into "AMD pays more for this architecture."** We measured a decay on our Vulkan
build, and there is a documented llama.cpp limitation on the *HIP* backend where this model's
sparse-attention top-k falls back through the CPU past ~1k context unless the build links CUB — a
build flag, not silicon. Whether Vulkan carries an equivalent limitation is untested. Until a
HIP + CUB arm is run, this is a property of the build we measured.

⚠ Also worth stating alongside any cross-class quote: **on the Strix node the 27B beats Flash-Next
on agentic work on both axes** (~27 vs ~24 t/s decode, and 10.6 s vs 24.9 s TTFT on the
large-prefill turn), which reproduces the same verdict reached independently on the GB10 box.
**But read only the TTFT half as solid.** The 27B row was measured a week earlier on an older engine
build — same box and same instrument, but not the same day — and a ~12% decode difference sits at
the edge of the ±10% band this repo publishes for exactly that reason
([how-to-read §3](how-to-read-these-numbers.md#3-our-10-day-and-composition-band), whose first rule
is that no sub-10% claim is judged against another day's baseline). The 2.4× TTFT gap is far outside
the band and is the load-bearing half.

### Concurrency, and why the answer depends on depth

Same replay instrument as §2, unmodified, so the two models sit on one instrument. Two campaigns:
**2026-09-08 at ~6.5k context**, and **2026-09-09 at ~33k, ~45k and ~59k** — the depths at which
repo-sized agent work actually runs. Shipped configuration both times, one boot, no restart.

| Context | Clients | Admitted at once | Aggregate, full window | Aggregate, steady state | Per client |
|---|---|---|---|---|---|
| ~6.5k | 1 | 1 | 21.8 t/s | 21.8 | ~22 t/s |
| ~6.5k | 4 (n=2) | 4 | **40.3 / 43.9** | 55.5 / 57.0 | ~15 t/s |
| ~6.5k | 8 (n=2) | 8 | 43.9 / 41.5 | 52.5 / 35.8 | ~7 t/s |
| ~33k | 1 (n=2) | 1 | **16.8 / 20.9** | not recorded | 17-21 t/s |
| ~33k | 4 (n=2) | 4 | **26.2 / 27.1** | not recorded | ~6.7 t/s |
| ~33k | 8 (n=2) | 8 | **31.1 / 33.6** | not recorded | ~4 t/s |
| ~45k | 8 | **6-7** | 12.4 t/s | not recorded | ~1.6 t/s |
| ~59k | 8 | **5** | 11.0 t/s | not recorded | ~1.4 t/s |

★ **"Saturates at four" is a property of shallow context, not of the stack.** At ~6.5k, four and
eight sessions are statistically identical, and past four sessions extra users divide the same work
rather than adding any. **At ~33k, eight sessions return ~22% more than four** (31-34 against 26-27,
both n=2). Each request is slower at depth, so more of them fit before the box is throughput-bound
and the saturation point moves outward. **Never quote a saturation claim without the depth it was
measured at.**

★ **The knee is a cliff, and it sits between 33k and 45k.** Going from ~33k to ~45k is 1.37× the
depth and costs **~62% of aggregate throughput** (31-34 down to 12.4). Going on to ~59k barely moves
it again (12.4 to 11.0). ⇒ **Keep per-user context under about 35k and eight users get ~4 t/s each;
past it, aggregate falls by roughly 60% and stays there.** ⚠ **Both deep rows are single runs.**
What supports the conclusion is that they agree with each other at two different depths and with
different generated-token counts, while sitting ~2.7× below a shallow pair whose own spread is 8% —
not the precision of either cell. Treat ~35k as a planning boundary, not a measured threshold.

Against the 27B flagship on the identical instrument, **comparing full-window aggregate to
full-window aggregate throughout**, this model runs at **64% at one session, 49% at four and 36% at
eight** — the gap widens under load. Those three cells are all at ~6.5k; **no 27B measurement exists
at depth**, so no cross-model comparison at 33k or beyond is possible. Declare the base: mixing
full-window against steady-state produces a different trio for the same runs.

⚠ **The steady-state aggregate is unreliable at eight sessions on this stack** (52.5 versus 35.8
across two batches) because its window closes when the first session finishes and output lengths
vary 5×. Quote the full-window figure at eight; both metrics agree at four.

⚠ **A single-session cell is a composition lottery.** The two ~33k one-session runs came out 24%
apart (16.8 and 20.9) on identical input, because the model generated 1,261 tokens in one and 1,903
in the other. Fixed per-session cost amortises over however much the model chooses to write, so more
generation reads as a higher rate. **Quote one-session figures as a range, or run n≥3.** Solving
those two runs together gives roughly **44 s of fixed overhead per five-turn session plus tokens at
~41 t/s** — two points, so indicative rather than a result, but it is the shape to plan with.

### Capacity: the measured pool, and what admission does when it runs out

**Measured KV pool: 514,149 tokens**, read from the running server's own startup log and internally
consistent with the same log's `Maximum concurrency … 1.96x` line (514,149 ÷ 262,144 = 1.96). It is
a measurement, not a calculation. The concurrency cap of 8 was confirmed the same way, from the
scheduler's own counters rather than from configuration.

⚠ **That figure is a property of the configuration, not of the model or the hardware.** It follows
from the memory fraction this recipe ships with, minus a ~76 GiB resident footprint. Change the
memory fraction, the checkpoint or the box and you get a different pool — read your own, using the
method in [§3](#3-context-and-capacity).

**The box declines to admit rather than admitting and evicting.** As depth rises, the scheduler
throttles how many requests it will run at once, and the excess waits:

| Context | Clients offered | Running at once | KV occupancy peak |
|---|---|---|---|
| ~33k | 8 | **8** | 91% |
| ~45k | 8 | **6-7** | **99%** |
| ~59k | 8 | **5** | 98% |

**No preemption occurred at any depth, and no request failed.** At ~59k, three of eight clients
queued for essentially the whole sixteen-minute run and still completed cleanly. Contrast the
flagship 27B stack, where a full pool produces silent retraction and re-prefill ([§3](#3-context-and-capacity)):
on this stack the same pressure surfaces as waiting, not as a latency spike with no error.

⚠ **Occupancy runs well above naive `sessions × depth`** — between **1.6× and 2.2×** across the
conditions measured, and the factor is **workload-shaped rather than constant**: single-turn
requests all sitting at full depth cost more per request than multi-turn sessions ramping up through
turns. **We have no confirmed mechanism for it.** Prefix-cache retention plus turn-to-turn growth is
a hypothesis, not a measurement, and it should not be quoted as a property of the engine.

### Correctness under load

Every figure above is a rate, and a rate says nothing about whether the answers were right. Measured
separately, 2026-09-09: **eight concurrent clients at ~33k — seven admitted at once at peak — each
retrieving a distinct planted fact, returned 8/8 correct with KV occupancy peaking at 98.6%.** A
same-day single-stream control passed on the same probe.

⚠ **Seven, not the eight the ladder admitted at the same depth — and the difference is the point.**
These are single-turn requests, which the occupancy note above explains cost more pool per request
than multi-turn sessions do. Same admission behaviour, harder shape — and a worked example of why
that factor is a range rather than a constant.

⚠ **Two limits on that result.** It covers **single-turn** retrieval only; correctness of prefix
reuse *across turns* — the growing-prefix pattern reported upstream to crash this architecture — has
not been inspected, though the replay ladder ran that pattern for hours with zero errors. And
retrieval of a planted fact is the easiest long-context task there is.

⚠ **The probe needs a real `max_tokens` budget or it measures nothing.** Run at a 16-token cap, the
same eight-wide test returned 0/16 — not because retrieval failed, but because the whole budget went
to reasoning before the answer arrived, and because 16-token generations finish at the end of prefill
so the sessions never actually overlapped. **The gate is asymmetric: a correct answer proves
retrieval, a wrong one proves nothing.** Any re-measurement of the depth cells above should use a
budget of at least 1024.

✅ **Raising the budget is what produced the 8/8 above.** The same probe, same server, same day, re-run
at **`max_tokens` 2048**, returned **8/8 at ~33k** — where the 16-token version had missed every one of
its 16 trials across two depths — and the same-day single-stream control missed at the tight cap too, which is what identified
the probe rather than the server as the fault.

**Longest session observed in any of these runs: 948 s of wall clock.** That is the measured basis
for the ≥1800 s client-timeout advice handed to callers; we ran with 1800 s throughout and therefore
**cannot say whether a 600 s default would have held**.

### ★ One deep request stalls everyone else

Three agentic sessions, measured with and without a fourth client running back-to-back 128k
prefills:

| | 3 sessions alone | plus one deep client | change |
|---|---|---|---|
| aggregate, full window | 29.9 t/s | **12.8 t/s** | **−57%** |
| the worst two of the three sessions, wall-inclusive | ~11.9 t/s | **~2.3 t/s** | **−80%** |
| session wall | 48-73 s | 222-306 s | **3-4× longer** |
| the deep client's own prefills | 60.2 s solo | 62-65 s | **−3 to −8% only** |

The scheduler serves the large prefill while decoding sessions wait, so the cost is **asymmetric**:
the deep user barely notices, everyone else pays. The comparison is conservative — the interfered run
generated *more* tokens (3,904 versus 2,166) and still finished at less than half the throughput.
**Two sessions spent ~220 seconds producing ~500 tokens each — a user would call that broken,
not slow.**

⚠ **The −80% is the worst-hit sessions, not an average.** The three sessions were not hurt equally:
two produced ~500 tokens each while the third kept a much larger share of the 3,904 total. The
all-sessions figure is the aggregate row's **−57%**. **Quote −57% for what a group loses and −80% for
what the unluckiest user sees** — and note the aggregate row already implies ~4 t/s per session on
average, not ~2.3.

⇒ **This caveat is about one request's prefill size, not about how many sessions are running.**
Whatever concurrency the depth table above supports, a single client submitting a large document
costs everyone else well over half their rate — and the worst-hit sessions roughly four fifths — for as long as that prefill runs — and on a
model chosen *for* large documents, assuming nobody does that is not safe planning. Size this stack
by depth (above), then assume the figure collapses whenever one user pastes something big, and put
shared agent traffic on the flagship 27B stack instead. An upstream mitigation exists (chunking long
prefills so decoders interleave) and **has not been tested here**; it needs a restart.

⚠ The interference test itself was run at ~6.5k with three background sessions, so the −57% and −80%
figures are measured at that depth. The mechanism — a large prefill being served while decoders wait
— is not depth-specific, but the magnitudes at ~33k are **not measured**. Expect them to be no
better: at depth the pool is already near full and there is less room to absorb a large prefill.

### Against published claims for this stack

Stated as theirs, not ours. The upstream recipe's README claims **~26 t/s decode and 2,400-2,900 t/s
prefill** on a warm page cache. A separate first-party engine cookbook publishes **~29 t/s at one
session** for a single-node NVFP4 configuration on this hardware class.

**Both of those decode figures sit below our 33-36 band, and our prefill (~2,100 t/s) sits below
theirs** — roughly 12% under the bottom of their range and 28% under the top. ⚠ **We are not
"bracketed" by them**: two third-party results landing on the same side of ours is weaker evidence
than two straddling it. What they do support is
that our decode figure is not implausible for this stack, and that our prefill is if anything
conservative. Neither apparatus is described in enough detail to say why the decode figures differ.
