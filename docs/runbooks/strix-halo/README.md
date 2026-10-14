# Strix Halo (gfx1151) hardware class overview

Recipes in this folder were verified on **AMD Strix Halo (gfx1151)** nodes: a unified-memory APU with
~122 GiB of system LPDDR5X. **OS as verified:** Ubuntu, ROCm 7.2.4, **7.0.0 kernel series** — the
point release varied between the units used for verification, so treat the kernel as a class fact
rather than an exact pin. They should port to any unit of the same class, but every number below was
measured on ours, so re-measure before you quote one about a different box.

---

## What makes this hardware different

### 1. Memory is unified: CPU and GPU read the same LPDDR5X

There is no discrete card and no separate VRAM bus. The GPU's working set lives in the same physical
memory the CPU uses, reached over the same controllers.

The practical consequence catches people out: **moving a job "onto the CPU to protect the GPU" does not
buy bandwidth isolation here.** On a discrete-GPU box, a CPU-side embedding service and a GPU-side LLM
contend for almost nothing. On this hardware they contend for the one resource that actually limits
inference. Co-tenancy decisions have to be made on total bytes/second, not on which device the work
nominally runs on.

The same unification shows up in memory *accounting*: vLLM on this APU derives "free GPU memory" from
host free RAM, so an unrelated resident process changes what a serve command is allowed to reserve.

### 2. Effective bandwidth is per-format, not one machine number

A single "~212-214 GB/s" constant is commonly quoted for this hardware. That is a **4-bit** number, not a
machine number.
Measured bare (no drafter), a 27B-class model on llama.cpp Vulkan, single stream, 3-5 runs each,
run-to-run spread 0.00-0.02:

| Weights format | File size | ms/token | Implied effective bandwidth |
|---|---|---|---|
| K-quant 4-bit (`UD-Q4_K_XL`) | 17.92 GB | 82.3 | **~218 GB/s** |
| `Q8_0` | 29.05 GB | 127.2 | **~228 GB/s** (+4.9%) |

Mechanism: `Q8_0` is a flat 8-bit block format with one scale, while the 4-bit K-quant uses super-blocks
with multiple scales and per-layer mixing, so there is more dequantisation compute per byte read and less
of the memory bandwidth is usable.

**How to apply it:** when predicting decode throughput, pick the constant for the *format*. That is
~218 GB/s for K-quants, ~228 GB/s for `Q8_0`, and ~230 GB/s unquantized. Predicting a `Q8_0` model with
the 4-bit constant under-predicts by ~5%. ⚠ **Only the two rows above are measured**; the ~230 GB/s
unquantized figure extrapolates the trend, and **we have never measured a constant for BF16.**

These predictions hold well in practice. A BF16 MoE with ~4B active parameters reads ~8 GB/token,
predicting ~27-29 t/s across that bracket against ~24.5 measured (**roughly 85-90% of roofline**, a
bracket rather than a point, precisely because the BF16 constant is the one we do not have); the
same model at 4-bit reads ~3 GB/token,
predicting ~72 against 72.16 measured. A BF16 *dense* 31B reads its full ~62 GB every token, predicting
~3.5-3.7 t/s against ~3.5 measured.

### 3. The GTT pool must be sized, and some units arrive already configured

`rocm-smi --showmeminfo gtt` reports **VRAM total = 512 MB** (a small fixed carveout whose
percentage barely moves whatever you load, so a "VRAM 54%" reading is meaningless) and **GTT total = the real unified pool** where models live.
**Watch GTT, never the VRAM percentage.** We measured 28% VRAM with nothing loaded against 29% with an 8B
model resident.

The GTT ceiling is set by the TTM page limit, and **units of the same class can arrive provisioned
differently**: one node had `options ttm pages_limit=26214400` in `/etc/modprobe.d/*.conf`
(26,214,400 × 4 KiB = exactly 100 GiB), another had no such file and fell back to the amdgpu/TTM default
of 50% of RAM (~61 GiB). Same RAM, same chip, different as-shipped configuration.

**Check, do not assume:**

```bash
cat /sys/module/ttm/parameters/pages_limit     # 26214400 == 100 GiB
rocm-smi --showmeminfo gtt                     # expect GTT Total 107374182400 B
```

To raise it: add `options ttm pages_limit=26214400` to a file under `/etc/modprobe.d/`, run
`update-initramfs -u`, reboot. This needs sudo and a reboot, and it is reversible by deleting the line.
A BIOS-pinned UMA carveout is the fallback if dynamic allocation misbehaves.

### 4. Prefill is the cost centre, and CPU-side tuning is not a lever

For agentic workloads (long prompts, short answers, 20-40:1 input:output) the dominant cost is
single-stream **prefill at depth**, and it behaves nothing like decode. We measured a depth coefficient
of 1.68e-7 s per token per token-of-depth; at ~54k depth that term accounts for ~94% of prefill time.
Decode also degrades with depth: a 26B-class MoE that decodes ~24.5 t/s on short prompts measured
~16 t/s at 26k context — a figure measured on a wider context window than the Gemma recipe ships
(`--max-model-len 16384`), so it shows the shape of the depth penalty rather than a depth that
configuration accepts. **Always state the depth a decode rate was measured at.**

CPU-side optimisation is a closed category on this hardware. Three levers measured on the same node:

| Lever | Mechanism | Result |
|---|---|---|
| `tuned accelerator-performance` | remove CPU wake latency / C-states | prefill -0.1% (noise), decode +1.3% |
| HIP graphs (drop `--enforce-eager`) | remove CPU launch overhead | 167.93 to 168.44 t/s **aggregate at 32 concurrent** (Gemma-4 26B BF16 on vLLM, 256-token replies) = noise |
| `--max-num-seqs` 16 to 64 | feed the **GPU** more work | **+37%** — but the fix is to *delete* the flag, not to pick 64; see the recipe |

The reason is visible in the clocks: sampled during a sustained prefill under the stock `balanced`
profile, the GPU sat at ~2896-2898 MHz against a 2900 MHz top DPM state (99.9% of ceiling) at 94-100 W
with zero droop over 60 s. There is no clamp to escape, so a cure for CPU starvation returns nothing.
`amd_iommu=off` is the one boot-level parameter that measured real, and only barely: **+3.0% prefill**
on an 8B 4-bit model, everything else within noise.

The short list below is a reminder, not the evidence. The receipts — which of these were measured on
our own nodes and which were closed on published evidence without a run — are in
[tested-and-rejected.md](../../reference/tested-and-rejected.md), alongside every other approach
closed out on this hardware class.

**Dead ends, not worth re-testing:** `tuned` and CPU governor tuning, `--async-scheduling` (same mechanism as
the nulls above), and 4-bit KV cache (`-ctk q4_0 -ctv q4_0`, advice that exists to fit context onto a
24 GB graphics card, which is not our constraint).

### 5. A generation benchmark is blind to correctness *and* to serving overhead

`llama-bench` measures token throughput. A model stuck emitting a repeated filler token emits tokens at
full speed, so a corruption or loop bug produces a **perfectly healthy-looking benchmark number**. We
have a concrete instance: a 26B MoE GGUF that loops endlessly on the ROCm backend on this exact chip
(clean on Vulkan) benchmarks fine.

It is equally blind in the other direction. Bench numbers exclude HTTP handling, scheduling, prefix-cache
behaviour, streaming and client overhead. Our own harness showed a 1.74× wall-clock gain on a real agentic
task on which raw decode had improved **~2.3×**. The **2.6×** often quoted next to it is a different
instrument again: the synthetic code-generation cell.

> [!WARNING]
> **Never accept a bench figure as evidence of correctness, and never quote a bench number as a serving
> number.** They are different instruments measuring different things.

---

## Which recipe for which job

| Recipe | Engine | Headline (workload named) |
|---|---|---|
| [Qwen3.8-27B on llama.cpp Vulkan + DFlash2](qwen38-27b-llamacpp-vulkan-dflash2.md) | llama.cpp PR #27342 @ `5ecbe1ac1`, Vulkan | Single-stream speculative decoding. Prose generation ~22-24 t/s and code generation ~30-33 t/s, against ~12 t/s bare, one request at a time. A real agentic coding task ran 1.7× faster in wall clock. |
| [Qwen3.8-27B on ROCmFPX / ROCmI4 + embedded MTP](rocmfpx-rocmi4-mtp-qwen38.md) | ROCmFPX fork @ `c49ebdbd5`, HIP | A third-party int4 build with the drafter shipped inside the GGUF, so no separate drafter file is needed. Builds and serves correctly; measured **10-20% slower than the row above across the controlled workloads we ran**, with prose at draft depth 8 a level tie. ⚠ One exception exists and the runbook records it as unresolved: in a single n=1 agent loop ROCmI4 was 16% *faster* on decode, at the highest draft acceptance of that session. Kept for repeatability. |
| [Gemma-4 26B-A4B on vLLM](gemma4-26b-vllm-serving.md) | vLLM `0.23.1.dev0+rocm7.14.0a20260624` bundle | Multi-user serving. ~24.5 t/s single stream **on short prompts** — the same stack measures ~16 t/s at 26k context, so that figure does not survive being quoted at depth. ~168 t/s aggregate at 32 concurrent **measured at `--max-num-seqs 64`, though the recipe ships the flag absent (engine default 128)** and ~1.4 s time-to-first-token; the same 32 users get only **122 t/s at `--max-num-seqs 16`**, so the flag is the result rather than a detail. ~221 t/s at 64 concurrent with `--max-num-seqs 256`. |
| [Qwen3.8-Flash-Next 180B on llama.cpp Vulkan](qwen38-flash-next-180b-llamacpp-vulkan.md) | stock upstream llama.cpp build 10858, Vulkan | Long-context capability, **one user at a time**. Retrieval verified correct to 128k context. Prefill ~400 t/s at 8k falling to ~170 at 128k; decode ~24 t/s falling to ~12. A 128k prompt costs ~12 minutes before the first token. The 27B row above is faster on agentic work on the same node, on both decode and TTFT. |

> [!NOTE]
> **The code-generation cell for the Qwen stack spans roughly 30-51 t/s across these pages, and every
> one of those numbers is real.** The ~30-33 t/s above comes from a mixed code-generation prompt; the
> [DFlash2 runbook](qwen38-27b-llamacpp-vulkan-dflash2.md) tuning box records ~41 t/s at the shipped
> draft depth; and a *maximally draftable* pure-code prompt reaches ~50-51 t/s **at
> `--spec-draft-n-max 8`** (the comparison table in
> [the ROCmFPX runbook](rocmfpx-rocmi4-mtp-qwen38.md) and
> [reference/numbers.md](../../reference/numbers.md)). Three things account for the spread. **Prompt
> shape is the largest**: at a fixed `n-max 4` the same sweep puts pure code at ~41 t/s against
> ~31 t/s for code-with-explanation, **+32%**. Then **draft depth**, a flag you set: ~41 t/s on
> `n-max 4` against ~51 t/s on `n-max 8`, **+23%**. Smallest, an **apparatus offset of roughly
> +11-13% from enabling the chat template and explicitly disabling thinking** (see
> [reference/numbers.md](../../reference/numbers.md)). Name the prompt and the harness whenever you
> quote one of these cells, and treat the pure-code end as a ceiling rather than a planning figure.

**Choosing between them:** speculative decoding fills spare memory bandwidth while one request is being
answered, so its gain shrinks as concurrency rises. It belongs where one agent works alone: overnight
loops, batch jobs, a single developer's assistant. Continuous batching is the opposite. It wins by
amortising weight reads across many in-flight requests, which is what a shared endpoint needs.

Note the asymmetry between architectures under concurrency. A dense model reads the same weights for
every request, so it amortises perfectly and gets a large batching multiplier from a slow single-stream
base; an MoE activates different experts per request, so it reads more total weight per step and gets a
smaller multiplier from a much faster base. **Dense batches harder relatively; MoE serves faster
absolutely.**

---

## Before you launch anything large

Two checks, every time, because both failure modes are reported misleadingly:

```bash
rocm-smi --showmeminfo gtt          # what is actually resident
pgrep -af 'vllm|sglang|llama'       # what else is holding it
```

A model that will not load because another job holds the pool reports as a load or engine-init error,
not as an out-of-memory message.
