# Gemma 4 26B-A4B + MTP drafter on llama.cpp (DGX Spark / GB10)

```text
Verified against : DGX Spark class node (GB10 Grace Blackwell, arm64, compute capability 12.1 / sm_121, 128 GB unified)
Engine / build   : llama.cpp mainline b10731, commit 0eadefebd, built CUDA 121a-real
Date verified    : 2026-09-01
```

Gemma 4 26B-A4B (MoE, ~4B active) served with a **multi-token-prediction (MTP) drafter**. On the
fixed-transcript agentic replay this is the **fastest agentic result we have measured on any
hardware class in this repo**: ~81 t/s with thinking off, ~87 t/s with thinking on.

> ⚠ **This is a capability comparison, not a like-for-like model A/B.** The replay ladder below runs
> the same prompts through different models, so the generated content differs. Qwen3.8-27B is the
> stronger reasoner (it leads on GPQA, HLE, LiveCodeBench v6 and MathVision, and is the only model
> of its size in the Code Arena top 10). Read this as a **point on the quality-vs-speed curve**, not
> as one model beating another.

---

## 1. Build

Same build line as the other llama.cpp recipes on this hardware class, with no sudo and no Docker:

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp   # skip if you already have it
cd ~/llama.cpp
cmake -B build-cuda -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=121a-real \
      -DGGML_CURL=OFF
cmake --build build-cuda -j --target llama-server
```

§3 invokes `~/llama.cpp/build-cuda/bin/llama-server`, so build in that tree or adjust the path.

`121a-real` is required; building for `120` fails with "no kernel image". Skipping curl avoids
`libssl-dev` and `clang`, and therefore avoids sudo entirely.

## 2. Model + drafter: quant choice is a first-class decision

| role | repo | file | size |
|---|---|---|---|
| target | `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` (ungated) | `gemma-4-26B_q4_0-it.gguf` | **~14.4 GB** |
| drafter | `unsloth/gemma-4-26B-A4B-it-GGUF` | `mtp-gemma-4-26B-A4B-it.gguf` | **~462 MB** |

⚠ **The MTP drafter lives at the REPOSITORY ROOT, not in an `MTP/` subdirectory.**

```bash
mkdir -p ~/models/gemma4-26b && cd ~/models/gemma4-26b   # §3 serves from here

hf download google/gemma-4-26B-A4B-it-qat-q4_0-gguf --local-dir .
hf download unsloth/gemma-4-26B-A4B-it-GGUF --include "mtp-*" --local-dir .
```

**Confirm on the node:** the exact filenames were not recorded verbatim. Check them against the
repository listings before trusting the download, and write the real ones back into this recipe.
`hf` is not part of a base OS install — a user-level virtualenv with `huggingface_hub` provides it
without sudo.

### ★ Quant sets bare speed on an MoE

Measured on an identical rig, same day, bare decode (no drafter):

| quant | bare decode |
|---|---|
| `unsloth` UD-Q4_K_XL (~17 GB) | **~47 t/s** |
| Google QAT `q4_0` (~14.4 GB) | **~59.5 t/s** |

The difference is **which tensors get upweighted**. The UD quant raises precision on the
attention, router and shared tensors, the ones read on **every** token, costing ~21% throughput
(~4.9 vs ~3.9 GB read per token). On a dense model that trade is small; on an MoE where only ~4B
params are active, the always-read tensors dominate the per-token read, so the choice is worth ~25%.
`-lm none` was inert (59.2 vs 59.5).

⚠ A forum figure of 85.5 t/s bare on this model **could not be reproduced** (thin post, no
methodology). Its **MTP multiplier** did reproduce (below).

## 3. Serve

```bash
~/llama.cpp/build-cuda/bin/llama-server \
  -m ~/models/gemma4-26b/gemma-4-26B_q4_0-it.gguf \
  -md ~/models/gemma4-26b/mtp-gemma-4-26B-A4B-it.gguf \
  --spec-type draft-mtp \
  --spec-draft-n-max 3 \
  -ngl 999 -ngld 999 \
  -c 40960 -np 1 -fa on --jinja \
  --chat-template-kwargs '{"enable_thinking":true}' \
  --alias <alias> \
  --host 127.0.0.1 --port 8090
```

**Your choice:** `~/models/gemma4-26b/` is illustrative; point `-m` and `-md` at wherever you keep
weights. `<alias>` is likewise yours to pick, not a value we failed to record (see the flag note
below).

- **`--spec-type draft-mtp` with `--spec-draft-n-max 3`** is the measured configuration.
- **`-ngl 999 -ngld 999`** puts all layers of **both** the target and the drafter on the GPU, as every
  other llama.cpp recipe in this repo does. The rates below are only reachable with both models
  resident on the GPU, so **confirm the offload lines in the startup log** rather than trusting a
  default. A CPU-resident drafter is the likeliest cause of a run that reproduces nothing here.
- **`--chat-template-kwargs '{"enable_thinking":true}'`** toggles thinking. Set `false` (or omit) for
  thinking off. Verify it is genuinely on by checking for `reasoning_content` in a response, and do
  not assume from the flag.
- `--alias` is cosmetic. Setting it to whatever name your replay bodies and client config already
  use lets both run unmodified.
- `-np 1` gives a single slot. A concurrency ladder for this model was **not measured**.
- ⚠ **Port 8090 is shared with the other two llama.cpp recipes here** — only one can run at a time, and
  the teardown `pkill` matches whichever it is. Change `--port` to run two.

**Cross-vendor drafter/target pairing works**: an `unsloth` MTP drafter drives a Google QAT target
with no compatibility work.

## 4. Readiness gate: HTTP status code

> [!WARNING]
> `llama-server` binds its port before the model finishes loading and answers `503 Loading model`
> until it is ready. A port check passes long before the server can serve.

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/health)" = "200" ]; do
  sleep 5
done
```

Gate on the **status code**, not the port and not a body grep.

## 5. Measured performance

Instrument: the same **fixed-transcript agentic replay** used for every stack here (one real captured
5-turn `opencode` session, replayed identically, warm-up discarded, n=3). Pooled decode = all
generated tokens ÷ all decode seconds.

| stack, identical replay | pooled decode |
|---|---|
| Strix Halo (gfx1151), llama.cpp + DFlash2 | ~27 t/s |
| Qwen3.8-Flash-Next 180B, same GB10 node | ~26 t/s |
| Qwen3.8-27B Q4 + DFlash2, llama.cpp | ~33 t/s |
| Qwen3.8-27B NVFP4 + DFlash2, SGLang | ~41 t/s (day band ~35-41) |
| **Gemma 4 26B QAT q4_0 + MTP, thinking OFF** | **~81 t/s** (runs 76.5 / 82.2 / 84.8) |
| **same, thinking ON** | **~87 t/s** (runs 85.7 / 86.0 / 90.2) |

Speculation and other cells:

| cell | measured |
|---|---|
| Code smoke with MTP | **~96.7 t/s**, acceptance **68.1%** (171/251, byte-identical across 3 runs) |
| Speculative multiplier over bare | **~1.63×** (59.5 to 96.7) |
| Serving prefill | **1,650-2,470 t/s** |
| TTFT, cached turns | sub-second (llama.cpp prefix cache) |
| Hard-task decode, thinking on | 86-101 t/s on every substantive turn |

⚠ **The serving-prefill range spans the replay's turns, and the prompt depth of each turn was not
recorded.** Prefill rate moves with depth, so 1,650-2,470 is a spread across unknown depths rather
than a rate: do not plan against a point inside it.

### ★ Thinking is nearly free on light agentic work

Thinking ON vs OFF on the replay: **rate +7%, tokens +11%**. Reasoning traces draft superbly on the
MTP head, so the extra tokens are produced faster than the baseline mix, and the speculative gain
very nearly pays for the extra work. This reproduces the same "thinking drafts well" finding seen on
a completely different model and drafter family (Qwen3.8 + DFlash2).

⚠ **On hard tasks the inflation is real**: ~6.3k vs ~2.6k tokens (~2.4×). The *rate* stays 86-101
t/s, but wall-clock grows. "Nearly free" applies to light agentic work, not to hard reasoning tasks.

### Quality gates passed (thinking mode)

- Word-frequency agent task: **5/5** first try.
- Hard task (`jsonpick`: wildcards, defaults, error handling): **7/7 first try**, plus an unprompted
  self-refactor pass that re-tested green.

## 6. What we did not measure

- Concurrency (`-np 4` / `-np 8`), which is the serving question, is not answered here.
- MTP flag mini-sweep (draft p-min): a related head measured +10.5% elsewhere, untested here.
- QAT-vs-UD **quality** screen (perplexity). The quant comparison above is a speed result only.
- A BF16 / vLLM parity arm for production-equivalence quality evidence.

---

## 7. Teardown

```bash
pkill -f "llama-server.*8090"
```

---

**See also.** Every figure above is a replay number before it is a rate, and the conventions behind
that — instrument, warm-up, day band — are set out once in
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md). The same cells sit
beside every other stack's in [numbers.md](../../reference/numbers.md), and
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md) is where the choice
between them is actually made.
