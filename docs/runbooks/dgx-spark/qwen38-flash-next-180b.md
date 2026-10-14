# Qwen3.8-Flash-Next 180B on one node (DGX Spark / GB10)

```text
Verified against : DGX Spark class node (GB10 Grace Blackwell, arm64, compute capability 12.1 / sm_121, 128 GB unified, 1 TB NVMe)
Engine / build   : llama.cpp mainline b10731, commit 0eadefebd (qwen4exp arch, upstream PR #27742 merged), built CUDA 121a-real
Date verified    : 2026-09-01
```

A **capability recipe**: a 180B-class Qwen4-architecture preview model running on a *single* node,
no Docker and no sudo, at usable speed. It completed a real agentic task correctly (5/5 tests).

> **Honest conclusion up front:** this is a **rewrite / refactor and long-context engine, not an
> agent driver.** On the identical agentic replay it pooled ~26 t/s against ~33 t/s for
> [Qwen3.8-27B + DFlash2](qwen38-27b-llamacpp-dflash2.md) on the same node, which is **−22%**. The
> 27B with speculative decoding stays the agentic engine; Flash-Next buys capability and
> intelligence-per-token.

---

## 1. What the model actually is

- **MoE, not dense**: ~125B main model (512 experts, top-10 routing, **~6B active**) plus a **~51B
  lookup-only n-gram embedding table** plus a **4B MTP head**, so **~180B of weights in total** — 125B + 51B + 4B. (Published totals vary
  by which of the three you count: 125B, 176B and 180B all appear, and all three are defensible.)
- Qwen4-architecture preview: 36 GDN linear-attention layers and 12 QSA sparse-attention layers.
- Quant used: **`unsloth` UD-Q4_K_XL GGUF, ~104 GiB across 4 shards** (not unquantized).
  ⚠ **That is GiB — the publisher lists 111 GB.** `du` reports GiB and model cards report decimal GB,
  so the same download legitimately reads as both. This page quotes GiB throughout, because GiB is
  what you will see on the node.
- Disk requirement in the 105-126 GiB range; first start streams 100+ GiB from disk.

The 51B table being **lookup-only, never computed,** is the entire reason this fits. Streaming MoE
*experts* from slow storage is measured-dead on this hardware class; streaming a lookup table is not.

## 2. Build (fresh tree, no sudo)

The qwen4exp architecture landed in mainline at b10731; older validated trees will not load the
model. Build it in its **own directory** so that any llama.cpp tree already on the node stays
untouched.

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llamacpp-flash-next
cd ~/llamacpp-flash-next
cmake -B build-cuda -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=121a-real \
      -DGGML_CURL=OFF
cmake --build build-cuda -j --target llama-server
```

**Your choice:** `~/llamacpp-flash-next` is illustrative. Any directory works, as long as it is separate
from any tree the other llama.cpp recipes in this folder may already have built on the node.

`121a-real` is required on GB10 (building for `120` fails with "no kernel image"), and
`-DGGML_CURL=OFF` keeps the build free of `libssl-dev` and `clang`, and therefore free of sudo.

## 3. Pull the model

`unsloth` UD-Q4_K_XL, 4 shards, ~104 GiB (111 GB) total. **Budget for a long download** — no transfer
rate was recorded, and the wall clock is a property of your link, not of the model.

```bash
mkdir -p ~/models/qwen38-flash-next
hf download unsloth/Qwen3.8-Flash-Next-GGUF --include "*UD-Q4_K_XL*" \
  --local-dir ~/models/qwen38-flash-next
```

**Confirm on the node:** the exact repository and shard filenames were never recorded here, only the
shape (4 shards, UD-Q4_K_XL). **Check the include pattern against the repository listing before
starting a 104 GiB download**, and write the real values back into this recipe. `hf` is not part of a
base OS install — a user-level virtualenv with `huggingface_hub` provides it without sudo.

## 4. Serve

```bash
~/llamacpp-flash-next/build-cuda/bin/llama-server \
  -m ~/models/qwen38-flash-next/<first-shard>.gguf \
  -ngl 999 -c 40960 -np 1 -fa on --jinja -t 20 \
  --spec-type ngram-mod \
  --load-mode mmap \
  --override-tensor per_layer_token_embd=CPU \
  --alias <alias> \
  --host 127.0.0.1 --port 8090
```

**Confirm on the node:** the shard filenames were never recorded, only the shape. Point `-m` at the
**first** of the 4 UD-Q4_K_XL shards (the set totals ~104 GiB, typically named
`...-00001-of-00004.gguf`); llama.cpp picks up the rest. Write the real filename back into this
recipe once you have it.

**Your choice:** `~/models/qwen38-flash-next/` is illustrative, and `<alias>` is the served model id
you want your clients to use, not a value we failed to record.

The settings that make this run:

- **★ `--override-tensor per_layer_token_embd=CPU` is the critical one.** It pins the large
  per-layer token embedding table to CPU/host memory so that **only the experts and attention
  occupy GPU memory**. Without it the model does not fit.
- **`--load-mode mmap`** keeps the table memory-mapped from NVMe rather than read into RAM.
- **f16 KV cache** (the default here; do not quantize it). A known `self_k_rot` assertion is tied to
  **quantized** KV; with f16 KV it **never fired** across the whole session.
  `LLAMA_ATTN_ROT_DISABLE=1` was exported as belt-and-braces only and was not needed.
- **`--spec-type ngram-mod`** is the copy/n-gram drafter. It is what produces the ~82 t/s edit cell
  below; it does nothing on fresh content.
- `-t 20` matches the 20 CPU cores on this hardware class.
- ⚠ **Port 8090 is shared with the other two llama.cpp recipes here** — only one can run at a time, and
  the teardown `pkill` matches whichever it is. Change `--port` to run two.

## 5. ⚠ Readiness gate: health endpoint, not the port

> [!WARNING]
> **This server binds its listening port BEFORE the model finishes loading.** With a ~104 GiB mmap'd
> model that gap is minutes long: an `ss -tln` gate fired minutes early and the first probe returned
> `{"error":{"code":503,"message":"Loading model"}}`.

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/health)" = "200" ]; do
  sleep 15
done
```

Gate on the health endpoint's **status code**, and **never on the port being open**. `llama-server`
also reports `{"status":"ok"}` in the body, so a body grep happens to work here, but the status-code
form is what the rest of this folder uses because it is the only form that also works against
SGLang (whose `/health` returns 200 with an empty body).

## 6. Measured performance

Instrument: the same **fixed-transcript agentic replay** used for every stack on this node (one real
captured 5-turn `opencode` session, replayed identically), plus single-cell code/prose/edit probes.
All cells in the "this model" column were taken the same day on one server config. The comparison
column is the [llama.cpp + DFlash2 recipe](qwen38-27b-llamacpp-dflash2.md) on this node, verified
2026-09-01, and it is a stack comparison rather than a matched interleaved run.

| cell | this model | Qwen3.8-27B + DFlash2, llama.cpp, this node, 2026-09-01 |
|---|---|---|
| Pure code, 256 tok (n=1) | **23.0 t/s** | ~41.5 t/s |
| Prose, 512 tok (n=1) | **~23.0 t/s** | not measured on that stack |
| **Agentic replay, pooled (n=3)** | **~26 t/s** (25.76; runs 21.05 / 21.50 / 35.19) | **~33 t/s** |
| **File-edit / rewrite (n=3)** | **64.3 / 81.9 / 82.4, warm ~82 t/s** | not measured |
| TTFT, large-prefill replay turn | 4.97-4.99 s | 4.22-4.25 s |
| Free-run agent task | completed, 5/5 plain-assert tests | completed, 5/5 |

⚠ **There is no prose figure for the comparison stack on this node.** The ~24-25 t/s prose cell that
circulates alongside these numbers is from the SGLang + NVFP4 + DFlash2 stack, not from llama.cpp,
and a similar-looking prose figure exists only for a different hardware class in a different
measurement session.
Neither belongs in this table, so the cell says "not measured".

**The profile in one line: ~22-26 t/s on anything that generates fresh content; ~82 t/s when the
answer already exists in the context.**

The mechanism is visible in a single row. One replay turn hit **51.5 t/s**, the turn where the model
re-emitted file content, i.e. the copy-drafter engaging. Every other replay turn sat at the 19-23 t/s
bare floor. In a real agent session the copy-drafter engaged in **roughly 1 turn out of 15**.

Warm edit runs (81.9 / 82.4) reproduce the published ~88.5 figure within apparatus differences.
Pre-registered replay band 25-40 held at the low edge; the sub-20 falsifier was not triggered.
Published code/prose predictions overestimated our result by ~30%, because their bench apparatus
differs (cold cache, different config); direction and mechanism were right.

## 7. Where this model fits

- **Refactors and rewrites** at ~82 t/s.
- **Long documents up to the context you configure — and this recipe ships at 40k** (`-c 40960`), so
  as written it will reject a 128k prompt outright. **No depth probe has been run on this stack at
  all**; every measured long-context figure for this model on this hardware class comes from the
  [vLLM + NVFP4 sibling recipe](qwen38-flash-next-180b-vllm-nvfp4.md), a different engine and quant.
  Raise `-c` if you need more, and measure it yourself. (A published 128k-token-in-56s figure was **not re-verified by
  us**).
- **Largest-model-on-one-box** work where ~22-26 t/s is acceptable. ⚠ **Largest, not established as
  better.** We have not shown this model is "smarter" than the 27B, and the only controlled
  head-to-head we know of favours the 27B. Do not repeat the "bigger model, therefore better answers"
  pitch without a test behind it — see
  [which-model-for-which-job.md](../../reference/which-model-for-which-job.md).
- **Not** the agent driver: the 27B with DFlash2 is **~28% faster** on the identical agentic replay
  (~33 against ~26 t/s — the same gap the intro states as this model running ~22% slower).

Kernels for this architecture were days old at time of measurement, and ~22 t/s bare for ~6B active
params has obvious headroom. Worth re-checking as the architecture matures.

---

## 8. Teardown

```bash
pkill -f "llama-server.*8090"
```

Removing the model directory (~104 GiB) and `~/llamacpp-flash-next/build-cuda` returns all disk.

---

**See also.** Before quoting anything from section 6, read
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md): it holds the
conventions this page assumes rather than restates. The cross-stack table the comparison column is
drawn from is [numbers.md](../../reference/numbers.md), and the Flash-Next-versus-27B decision is
made in one place, [which-model-for-which-job.md](../../reference/which-model-for-which-job.md).
