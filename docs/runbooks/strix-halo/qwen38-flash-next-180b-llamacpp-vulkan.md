# Qwen3.8-Flash-Next 180B on a Strix Halo node: llama.cpp (Vulkan), n-gram table on NVMe

```text
Verified against : AMD Strix Halo (gfx1151) node, 128 GB unified memory (~122 GiB usable,
                   100 GiB GTT pool), model on local NVMe
Engine / build   : stock upstream llama.cpp, Vulkan backend, build 10858 (commit 64e9bceb2),
                   built on the node with -DGGML_VULKAN=ON -DGGML_CURL=OFF. No fork.
Model            : AtomicChat/Qwen3.8-Flash-Next-GGUF, AD-4.27bpw-Q4_K_M-M64
                   (33 shards, 89 GiB on disk)
Date verified    : 2026-09-08
```

A **capability recipe**: a 180B-class model, correct at 128k context, on one consumer-class APU
node, with no fork, no Docker and no sudo.

> [!IMPORTANT]
> **Honest conclusion up front. This is a long-context reading tool for ONE patient user.**
> It answers correctly at every depth we tested, but a 128k-token prompt costs about **12 minutes**
> before the first token. On the same node, [Qwen3.8-27B + DFlash2](qwen38-27b-llamacpp-vulkan-dflash2.md)
> is faster on agentic work on **both** axes — ~12% on decode and **2.4× on time-to-first-token**.
> Choose this model for depth, never for responsiveness.

> [!WARNING]
> **One request at a time.** llama.cpp serves a single slot in this configuration and concurrency
> is **unmeasured** here. A second request queues behind the first — and behind a 128k prefill that
> is a twelve-minute stall. Do not put this behind a shared endpoint without measuring it first.

## 1. Why it fits on a 128 GB box

The model is **125B parameters with ~6B active**, plus a separate **51B-parameter n-gram /
per-layer-embedding (PLE) table** and a **4B MTP head** — **~180B of weights in total**, which is
where "180B" in this page's title comes from.

> [!NOTE]
> **Published totals for this model differ because they draw the fence in different places**: 125B is
> the main model alone, 176B adds the n-gram table, 180B adds the MTP head as well. All three appear
> in the literature and none is wrong — say which you mean, and never quote a bare total.

In this 4.27bpw quant the table is **38.4 GB (≈35.8 GiB)** and sits in its own GGUF shard; in BF16 it
is 102.5 GB (95.4 GiB). ⚠ Published quant footprints come in decimal GB while `du` reports GiB, so
both units are given here.

That table is **looked up, never computed**, so it stays on SSD and pages in on demand: a small
number of reads at deterministic addresses per forward pass, negligible against a per-token budget
measured in tens of milliseconds. Streaming MoE *experts* from storage is
hopeless; streaming a lookup table is not. That single property is the whole recipe.

> **Not measured: the actual read rate against the device.** We never instrumented it.

The quant we use isolates the table in **its own GGUF shard**, which is what keeps mmap paging
clean. Published footprint: **54.5 GB in memory + 38.4 GB on SSD = 92.9 GB total**. Measured on our
node: available RAM fell to **65-66 GiB free** at load, i.e. **≈55 GiB resident**, then 61 GiB with a
128k KV allocated. ⚠ **That is GiB** (what `free` reports) against the publisher's decimal **GB**:
55 GiB is 59 GB, close to their 54.5 GB figure but not a match. Do not set the two against each
other without converting.

---

## 2. Prerequisites

- A Strix Halo (gfx1151) node with **~122 GiB usable RAM**; **~110 GB free on local NVMe** (the model
  is 89 GiB = 95.6 GB, and a parallel download writes temporaries alongside it).
- **The model must sit on local NVMe.** On network storage llama.cpp issues pathologically small
  reads and prefill collapses to well under a token per second.
- A Vulkan SDK. If the node does not have one, the userspace tarball install in
  [the 27B Vulkan recipe](qwen38-27b-llamacpp-vulkan-dflash2.md) applies unchanged — no sudo.
- **The Hugging Face CLI (`hf`)**, used by the download in §4 and not part of a base OS install. A
  user-level virtualenv with `huggingface_hub` in it provides the command without sudo. ⚠ Install it
  **before** §4 — that is the 89 GiB step.
- **No sudo at any step.**

---

## 3. Build: a fresh tree, and it must be b10731 or newer

The `qwen4exp` architecture merged upstream on 2026-08-27 (llama.cpp PR #27742); older trees will
not load the model at all. **b10731 also carries a fix to a redundant transpose in this
architecture's head summation, worth ~32% on the context-length slope**, so treat it as the floor
even if an older build claims to load.

> [!WARNING]
> **Build into a new, clearly-named directory.** A node that has been used for this work may
> already carry several `llama-server` builds, some of them printing version strings in older
> formats. Reusing an ambiguous one is a wasted
> afternoon waiting to happen. Check what you have first:
>
> ```bash
> for f in $(find ~ -maxdepth 4 -name llama-server -type f 2>/dev/null); do
> echo "$(basename $(dirname $(dirname $f))) $($f --version 2>&1 | grep -o 'version: [0-9]*')"
> done
> ```

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llamacpp-fn
cd ~/llamacpp-fn
cmake -B build-vulkan -DGGML_VULKAN=ON -DGGML_CURL=OFF
cmake --build build-vulkan -j --target llama-server
```

**Your choice:** `~/llamacpp-fn` is illustrative; any directory works as long as it is separate from
existing build trees. `-DGGML_CURL=OFF` keeps `libssl-dev` and `clang` out of the build, which is
what makes it sudo-free.

### If cmake cannot find Vulkan

A common shape on these nodes: the Vulkan SDK lives in the home directory rather than on the system
paths, so `find_package(Vulkan)` fails with `Vulkan_LIBRARY / Vulkan_INCLUDE_DIR / glslc` missing —
even on a node that has built Vulkan binaries before. **If the node carries an earlier working
Vulkan build, read the paths off it rather than guessing them** (on a node with no such build, take
them from wherever you installed the SDK per the prerequisites above):

```bash
find ~ -maxdepth 4 -name CMakeCache.txt > ~/cc.txt 2>/dev/null
xargs grep -h "^Vulkan_" < ~/cc.txt | sort -u | sed "s|/home/[^/]*|~|g"
```

Then pass them explicitly, and **delete the failed build directory first** or the stale cache keeps
its NOTFOUND values:

```bash
export VULKAN_SDK=$HOME/vulkansdk/<version>/x86_64
export PATH=$VULKAN_SDK/bin:$PATH
cd ~/llamacpp-fn
rm -rf build-vulkan
cmake -B build-vulkan -DGGML_VULKAN=ON -DGGML_CURL=OFF \
  -DVulkan_INCLUDE_DIR=$VULKAN_SDK/include \
  -DVulkan_LIBRARY=$VULKAN_SDK/lib/VulkanLoader/lib/libvulkan.so \
  -DVulkan_GLSLC_EXECUTABLE=$VULKAN_SDK/bin/glslc
cmake --build build-vulkan -j --target llama-server
```

The `x86_64` in that path is correct and not a copy-paste error: the Vulkan SDK tarball is x86_64
**host** tooling (`glslc` and friends) running on the APU's x86 cores, not something targeting the GPU.

**Confirm on the node:** `<version>` is whatever your SDK directory is named. Gate on the build
number before going further:

```bash
~/llamacpp-fn/build-vulkan/bin/llama-server --version 2>&1 | head -2
```

---

## 4. Fetch the model

```bash
hf download AtomicChat/Qwen3.8-Flash-Next-GGUF \
  --include "Qwen3.8-Flash-Next-AD-4.27bpw-Q4_K_M-M64/*" \
  --local-dir ~/models/fn-strix
```

> [!WARNING]
> **★ This repository is Xet-backed and it will take everything your link will give.** It fetches
> deduplicated chunks in parallel rather than streaming files one at a time. We measured
> **167 MB/s — about 1.3 Gbit/s sustained** — where older Hugging Face pulls on the same node ran at
> 11-29 MB/s. A pull at that rate can saturate a shared network link within minutes. **Run large
> pulls out of hours, or cap the worker count** (`--max-workers`, check `--help` for your client
> version).
> To put the model on a second node afterwards, **copy it node-to-node over the local network
> instead** — pull from the destination, do not push — which touches no internet link at all.

Gate on **counts, not size** (the model card reports GB while `du` reports GiB, so a size gate is a
number that never arrives):

```bash
ls ~/models/fn-strix/*/ | grep -c gguf                 # the gate: expect 33
find ~/models/fn-strix -name "*.incomplete" | wc -l    # informational only, see below
```

**The shard count is the gate: expect 33.** The second command is informational and has **no**
expected value — read the warning below before acting on whatever it prints.

> [!WARNING]
> **The shard count is the gate here. The `.incomplete` count is a weak second check, not a
> completeness proof.** `huggingface_hub` 1.x names partial downloads
> `<hash>.<random>.incomplete`, so such a file never shares a name with the finished file it belongs
> to. On the DGX Spark recipe we found **16 orphaned temp files next to a complete, fully verified
> checkpoint** — left by attempts interrupted days earlier. Treat a non-zero count as disk to
> reclaim and investigate, **not** as evidence the model is unusable, and treat a zero count as
> proving nothing on its own.
>
> ⚠ **We have not verified what layout this client version writes under a `--local-dir` download**,
> so no stronger gate is offered here than the shard count plus the server actually loading the
> model. If you need certainty before a long load, hash the shards against the publisher's listed
> checksums.

---

## 5. Serve

```bash
export VULKAN_SDK=$HOME/vulkansdk/<version>/x86_64
export PATH=$VULKAN_SDK/bin:$PATH
M=$(ls -d ~/models/fn-strix/*/ | head -1)
S=$(ls $M*00001-of-00033.gguf)
nohup ~/llamacpp-fn/build-vulkan/bin/llama-server -m "$S" \
  -ngl 99 -c 131072 --jinja -fit off \
  --host 127.0.0.1 --port <port> > ~/fn.log 2>&1 &
```

Point `-m` at the **first** shard; llama.cpp picks up the other 32. Bind loopback and reach the node
over a private network or a tunnel, as every recipe in this folder does.

**Four flags carry this configuration:**

- **`-fit off` is required.** llama.cpp's automatic parameter fitting mis-sizes this architecture and
  fails to allocate.
- **mmap stays on** — that is what makes the n-gram table pageable. Never `--load-mode none`, never
  `--no-mmap`; non-mmap loading exhausts system memory on this hardware.
- **No `--override-tensor` is needed.** The table goes host-side by itself. We verified this: with
  `-ngl 99` and no override, load was clean and resident memory landed on the published figure.
- **f16 KV. Do not quantise it.** On this model quantised KV enables a Hadamard attention rotation
  the sparse-attention path lacks, and it measures *slower* — 66.0 t/s at q8_0 against 82.0 at f16,
  **a third-party figure, not ours**. The instruction is what our own evidence supports; the numbers
  are theirs.

### Readiness gate: status code, never the port

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:<port>/health)" = "200" ]; do
  sleep 15
done
echo FN-UP
```

`llama-server` **binds its port before the model is loaded** and answers 503 until ready, so a port
check fires minutes early. Load itself is fast — under a minute on a warm page cache.

---

## 6. Measured numbers

Instrument: a needle planted at 50% depth in randomised filler, **independent random filler per
request** so no two share a prefix, with retrieval verified rather than assumed — on this model a
wrong answer arrives as a normal 200 response. Token counts from the server's `usage` field.

| context | prefill | decode | retrieval |
|---|---|---|---|
| 8k | **~400-420 t/s** | **~24-25 t/s** | correct |
| 32k | ~360 t/s | ~21 t/s | correct |
| 64k | ~270 t/s | ~16 t/s | correct |
| 128k | **~170 t/s** | **~12 t/s** | correct |

- **Retrieval correct at all four depths**, exact planted value each time, no corruption, no
  truncation, no rejection.
- **Both rates decay with context** — decode falls ~52% from 8k to 128k. Time to first token at
  128k is about **12 minutes**.
- Streaming is honest on this stack (one chunk per token at every depth), so these timestamps are
  read directly and agree with wall-clock.

### Same-node comparison against the 27B

Nothing differs but the model: same node, same captured 5-turn agentic transcript, same replay
harness.

| model on one Strix Halo node | pooled decode | TTFT on the large-prefill turn |
|---|---|---|
| Qwen3.8-27B + DFlash2 | **~27 t/s** | **10.6 s** |
| Qwen3.8-Flash-Next 180B | ~24 t/s | **24.9 s** |

⚠ The 27B row was measured on an earlier date and engine build — same node and instrument, but not
the same day, and this replay carries a ±10% day/composition band, so the decode difference sits at
the edge of significance. **The TTFT gap is well outside any noise band and is the load-bearing half
of the comparison.**

### What a user actually waits

| task | time to a complete answer |
|---|---|
| short question, ~1k context | ~1 minute |
| 30k document + a 1,000-token answer | ~2.5 minutes |
| 64k document + a 1,000-token answer | ~5 minutes |
| 128k document + a 1,000-token answer | **~14 minutes** |

### ⚠ What the depth decay does and does not prove

We measured that **decode decays with context on this Vulkan build**. That is the observation.
Attributing it to the hardware would be premature: llama.cpp issue **#27856** documents that on the
**HIP** backend this model's decode collapses past ~1k context because `supports_op` caps
TOP_K/ARGSORT at 1024 elements unless built against CUB — i.e. the sparse-attention indexer's top-k,
which runs over the whole context many times per token, silently falls back through the CPU. That is
a **build flag**, not silicon, and it is fixed with `hipcub-devel`, `rocprim-devel` and
`-DGGML_CUDA_USE_CUB=ON`. **Whether the Vulkan backend carries an equivalent limitation is untested.**
Until a HIP + CUB arm is run on the same node and model, write this as a property of the build.

---

## 7. Handing this endpoint to users

**The general client-side rules are not repeated here.** They live in
[connecting-clients.md §5](../../connecting-clients.md#5-getting-good-results) — deriving `max_tokens` from context minus
prompt, `max_retries = 0` with a timeout of at least 1800 s, and taking token counts from `usage`.
Hand that page over and point at §4. ⚠ **One figure there does not apply to this stack**: that page
quotes about a minute to first token at 128k, which is the DGX Spark vLLM number. **Here it is ~12
minutes** — say so explicitly, or a user reads the wait as a fault.

What is specific to **this** stack, and must come from you:

- **This configuration serves one request at a time.** A second caller queues behind the first — and
  behind a 128k prefill that is a twelve-minute stall. Users need to know they are sharing a single
  slot, because the symptom of contention here is indistinguishable from a hang.
- **What depth costs on this hardware:** prefill runs ~400 t/s at 8k and ~170 t/s at 128k — so about
  **25 s per 10k tokens at shallow depth, rising towards a minute per 10k by 128k**, where a full
  128k prompt is the twelve-minute wait above. Even so, the worst case we have
  documented on this stack — a 128k document with a 1,000-token answer, about fourteen minutes — still
  sits inside the 1800 s timeout connecting-clients.md §5 recommends, with better than 2× margin. Do not raise it
  on account of this stack; do make sure they set it.
- **The empty-answer trap is sharper on a reasoning model**: too small a `max_tokens` does not
  truncate the answer, it deletes it. Verified on this stack, and identical on the DGX Spark one.

---

## 8. Where this model fits, and what is untested

**Fits:** one person reading long documents; rewrites and refactors where the answer largely exists
in the context; "largest model we can run on owned hardware" work where minutes are acceptable.

**Does not fit:** agentic loops (the 27B is faster on this node on both axes), anything interactive,
and any shared endpoint.

**Untested here:** concurrency of any kind · MTP speculative decoding (fork-only for this model, and
measured as a **net loss on Vulkan** while working on HIP) · the HIP backend generally · quality
beyond retrieval-at-depth and task completion · any soak or stability run.

### Builds we did not choose

- **A ROCm-FP4 imatrix GGUF** — peaks at ~94.4 GiB of GTT against a 100 GiB pool, i.e. ~5.6 GiB of
  margin. The build above leaves far more headroom for context.
- **A vendor-optimised IU4 build with its own runtime** — the engineering behind it is real, and
  gfx1151 genuinely has native 4-bit integer WMMA. We passed on it because its published headline
  numbers ran **2-3× above what users measured in its own discussion threads across two consecutive
  releases**, a sibling build from the same publisher produced garbage output after a patch, it
  locks you into a private runtime on the HIP backend, and its own table shows the slowest base
  decode of the candidates.
- **vLLM or SGLang on this hardware** — no support exists for this architecture on gfx1151: no pull
  request, no issue, no user report. Every published gfx1151 result for this model is llama.cpp or a
  llama.cpp fork. (SGLang does ship AMD cells for this model, but for CDNA data-centre parts, not
  RDNA 3.5.) This is not a handicap: on this hardware our own apparatus-matched measurement had
  llama.cpp at **5.85× vLLM** on a comparable MoE.

---

## 9. Undo

```bash
pkill -f "llama-server.*<port>"
rm -rf ~/llamacpp-fn        # the build tree
rm -rf ~/models/fn-strix    # ~89 GiB
```

Nothing was installed on the host: no systemd unit, no packages, no sudo at any step.

---

**See also.** The depth ladder above follows the conventions in
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) — instrument first,
number second — and those conventions are what make the 27B comparison row readable at all.
[numbers.md](../../reference/numbers.md) is the cross-stack table that row is drawn from, and
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md) is where "depth, never
responsiveness" turns into an actual choice.
