# llama.cpp (Vulkan) + Qwen3.8-27B + DFlash2 speculative decoding

```text
Verified against : AMD Strix Halo (gfx1151) node, 128 GB unified memory
Engine / build   : llama.cpp PR #27342, commit 5ecbe1ac1, Vulkan backend
Date verified    : 2026-08-19 (tuning section re-measured 2026-08-25)
```

> [!IMPORTANT]
> **What this buys you: roughly 2× to 2.6× faster single-stream generation, at no quality cost.**
> Speculative decoding uses a small "drafter" model to guess several tokens at once, which the full model
> then verifies in a single pass. Guesses that survive verification are free. The full model still decides
> every token, so output quality is the full model's. You are buying speed, not trading accuracy for it.

> [!WARNING]
> **This is a single-stream lever.** The gain comes from filling spare memory bandwidth while one request
> is being answered. It shrinks as concurrency rises, and it is *not* the right tool for a shared endpoint
> serving many people. Use it where one agent works alone: overnight coding loops, batch jobs, a single
> developer's assistant.

## 1. What we measured

Target model `Qwen3.8-27B` at 4-bit, drafter at 8-bit, Vulkan backend, **one request at a time**, on one
gfx1151 node. Compare arms only within a single matched run, because absolute values move with prompt and
day.

| Workload | Without drafter | With drafter (`n-max 4`) | Ratio |
|---|---|---|---|
| Prose generation | ~12 t/s | **22.5 - 24.1 t/s** | ~1.9× |
| Code generation | ~12 t/s | **30.0 - 32.7 t/s** | ~2.6× |
| Decode at 13k context depth | ~12 t/s | **29 - 31 t/s** | ~2.6×, so **the speedup holds at depth** |
| Full turn at 13k depth (prefill + 512 tokens) | ~84 s | ~60 s | |

⚠ **This "code generation" cell and §7's "~41 t/s pure code" are both at `n-max 4` and both real.**
Two things separate them, and neither is draft depth. **Prompt shape:** within §7's own single-day
sweep, pure code ran ~41 t/s against ~31 for code-with-explanation, **+32%**. **Apparatus:** the two
cells come from different rigs on different dates, and the later one measured **+11-13% faster** on
identical prompts, from enabling the chat template and explicitly disabling thinking. Quote the row
whose prompt and harness match yours, and never the two as a range.

**On a real agentic coding task**, a headless agent writing a CLI tool, running its tests and fixing what
failed, we measured **~2m22s with the drafter against ~4m07s without, about 1.7× wall clock**, with ~75%
of drafted tokens accepted across 6 turns. Wall clock gains less than raw decode does, because prefill,
tool round-trips and client overhead cannot be sped up this way.

**Under JSON-schema-constrained output**, acceptance drops to 48-62% (from ~75%), so roughly 2× is
retained rather than ~2.6×.

> [!NOTE]
> **On "lossless":** the full model verifies every token, so quality is preserved *in distribution*. It is
> **not** bit-for-bit identical to running without the drafter. At temperature 0 we saw output diverge at
> a single near-tie token after several identical paragraphs. Both texts were fine. If you are measuring a
> run-to-run noise floor, measure it **with the drafter on, in the exact configuration you will serve.**

---

## 2. Prerequisites

- ~25 GB disk: 18 GB target model, 2 GB drafter, ~5 GB build and SDK
- A working Vulkan runtime (present on these nodes via the system RADV driver)
- **No sudo required at any step**

Everything below is userspace and reversible. See [Undo](#9-undo).

---

## 3. Build llama.cpp with Vulkan (one time, ~30-45 min)

### 3.1 cmake, if the node does not have it

```bash
pip install --user --break-system-packages cmake
```
```bash
export PATH="$HOME/.local/bin:$PATH"
```
```bash
cmake --version
```

### 3.2 Vulkan shader compiler (`glslc`), userspace

The build needs `glslc` to compile shaders. The Vulkan SDK tarball provides it **without sudo**. This
path is proven; do not reach for a package manager.

```bash
mkdir -p ~/vulkansdk && cd ~/vulkansdk
```
```bash
curl -L -o vulkansdk.tar.xz "<vulkan-sdk-linux-x86_64-tarball-url>"
```
```bash
tar xf vulkansdk.tar.xz
```
```bash
source ~/vulkansdk/<version>/setup-env.sh   # the tarball unpacks into a <version>/ directory
```
```bash
glslc --version
```

**Confirm upstream:** only the version was recorded, not the URL. We used **1.4.357.1**, which ships
`glslc` from shaderc v2026.3. Take the actual tarball link from the vendor's Linux SDK download page,
then write both the URL and the version you used back into this recipe.

> [!IMPORTANT]
> `setup-env.sh` must be **sourced in the shell you build in**. The finished server does **not** need it.
> It runs in a clean shell against the system Vulkan loader.

### 3.3 Clone and check out the DFlash2 PR

> [!IMPORTANT]
> **PR #27342 has since merged.** Mainline llama.cpp now ships `--spec-type draft-dflash` in normal
> releases (confirmed on a build used for the DGX Spark recipes), so the PR-branch checkout below is
> no longer required. It is kept because it is the exact build these numbers were measured on. For a
> fresh install, build current mainline instead and skip to 3.4. **Not measured:** DFlash2 from
> mainline on this hardware class. Expect the flags to be the same and re-verify the numbers.

The steps below pin the PR branch that the measurements in this runbook were taken on.

```bash
mkdir -p ~/dflash2 && cd ~/dflash2
```
```bash
git clone https://github.com/ggml-org/llama.cpp
```
```bash
cd llama.cpp
```
```bash
git fetch origin pull/27342/head:dflash2
```
```bash
git checkout 5ecbe1ac1
```

> [!NOTE]
> The commit is pinned deliberately so the numbers in this runbook are reproducible. Since PR #27342
> merged, a mainline build is the simpler route for a new install.

### 3.4 Build

```bash
cmake -B build-vulkan -DGGML_VULKAN=1
```
```bash
cmake --build build-vulkan --config Release -j
```

Result: `~/dflash2/llama.cpp/build-vulkan/bin/llama-server`

> [!TIP]
> **If you build the HIP/ROCm backend instead** (`-DGGML_HIP=1 -DCMAKE_HIP_ARCHITECTURES=gfx1151`), ROCm's
> linker may fail on a missing `libxml2.so.2`. Fix it without sudo by symlinking the system library into
> `~/lldcompat/libxml2.so.2` and putting that directory on `LD_LIBRARY_PATH` for **both** cmake steps,
> configure as well as build. This does not affect the Vulkan build.
>
> **Vulkan is the recommendation regardless:** on identical hardware it ran the speculative arms ~28-31%
> faster than HIP, with acceptance rates identical across both backends. The feared Vulkan long-prefill
> penalty did not materialise, and HIP's prefill edge was only ~7%.

---

## 4. Get the models

Two files: the model that answers (the *target*) and the small model that guesses (the *drafter*).

```bash
mkdir -p ~/models/qwen38-27b/dflash2-draft
```

**Target**, `Qwen3.8-27B`, unsloth dynamic 4-bit (~18 GB), from `unsloth/Qwen3.8-27B-GGUF`:

```bash
cd ~/models/qwen38-27b
```
```bash
wget -O Qwen3.8-27B-UD-Q4_K_XL.gguf "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q4_K_XL.gguf"
```

**Drafter**, the matching DFlash2 drafter at 8-bit (~2 GB), from `z-lab/Qwen3.8-27B-DFlash2-GGUF`. Save it
to a fixed short path so launch commands stay readable:

```bash
cd ~/models/qwen38-27b/dflash2-draft
```
```bash
D=https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2-GGUF/resolve/main
```
```bash
wget -O draft-q8.gguf $D/Qwen3.8-27B-DFlash2-Q8_0.gguf
```

Or the 4-bit drafter instead. It measures the same speed at half the size; see the tuning notes:

```bash
wget -O draft-q4km.gguf $D/Qwen3.8-27B-DFlash2-Q4_K_M.gguf
```

> [!NOTE]
> No `huggingface-cli` is needed. The `/resolve/main/<filename>` URL works directly with `wget`, and these
> repos are public, so no token is required.
>
> **The drafter must match the target model.** A DFlash2 drafter is trained against one specific model and
> cannot be mixed with a different one.

---

## 5. Run the server

```bash
cd ~/dflash2/llama.cpp
```
```bash
./build-vulkan/bin/llama-server \
  -m ~/models/qwen38-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf \
  -md ~/models/qwen38-27b/dflash2-draft/draft-q8.gguf \
  --spec-type draft-dflash \
  --spec-draft-n-max 4 \
  -ngl 999 -ngld 999 \
  -c 40960 -np 1 \
  --jinja \
  --host 127.0.0.1 --port <port>
```

Flag by flag:

| Flag | Why |
|---|---|
| `-md` | the drafter model |
| `--spec-type draft-dflash` | use the DFlash2 drafting method |
| `--spec-draft-n-max 4` | draft 4 tokens per pass. **Workload-dependent, see below** |
| `-ngl 999` / `-ngld 999` | put all layers of target **and** drafter on the GPU |
| `-np 1` | single stream, because this lever is single-stream by design |
| `--jinja` | use the model's own chat template; **required for tool calling** |
| `--host 127.0.0.1` | keep the engine off the network; reach it over an SSH tunnel |

> [!WARNING]
> **If you ever raise `-np` above 1, raise `-c` with it.** llama.cpp computes `n_ctx_seq = n_ctx / n_seq_max`
> unless unified KV is on, and the server only enables unified KV in AUTO mode, so passing `-np` explicitly
> turns it off and `-c` becomes a total budget that gets sliced. `-np 16 -c 32768` gives 2048 tokens per slot,
> which is HTTP 400 on every request at production prompt sizes. Use `-np N -c (16384*N)` and **read
> `n_ctx_seq` back from the startup log**, since it is also padded to 256 and capped at `n_ctx_train`.

### Confirm it is up

Gate on the **HTTP status code**:

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:<port>/health)" = "200" ]; do sleep 5; done; echo SERVER-UP
```

> [!IMPORTANT]
> **Neither the port nor a log string is a readiness signal.** `llama-server` binds its listening port
> **before** the model finishes loading and answers **503** until it is ready, so a port check reports
> "up" on a server that will reject every request. And ready-message wording changes between builds, so
> a stale grep pattern silently never matches, which reads exactly like a server that failed to start.
> The status code is the only gate that means what it says. The same rule holds on every engine and
> hardware class in this repo.

> [!NOTE]
> **This startup line is benign and expected:**
> `E ... dflash requires ctx_other (this warning is normal during memory fitting)`
> It appears during memory sizing and does not indicate a problem.

---

## 6. Is the drafter actually working?

Speed alone will not tell you. Check **acceptance**, the share of drafted tokens the target model kept.
The server reports draft counts per response (`draft_n` and `draft_n_accepted` in its timing output).

Expected on this stack:

| Workload | Acceptance |
|---|---|
| Code generation | ~65% |
| Prose | ~44% |
| Real agentic loop, thinking on | **~75%**, because reasoning traces draft *well* |
| JSON-schema-constrained output | 48-62% |

⚠ **§7 reports 88.9-96% on code and that is not a contradiction**: these rows come from one fixed
code-and-prose prompt, §7's from a maximally-draftable pure-code one. **Acceptance is a property of
the prompt.** Compare your own runs against the row whose shape matches your traffic.

Acceptance near zero means the drafter is not engaging. Check that `--spec-type draft-dflash` was accepted
and that `-ngld 999` put the drafter on the GPU.

---

## 7. Tuning notes

> [!IMPORTANT]
> **Re-measured 2026-08-25: `--spec-draft-n-max 4` is too low for code generation, and 8 is worth about
> +23% there. This supersedes the earlier "never raise n-max above 4" advice.**
>
> The earlier result was correct at the point it was measured and does not generalise beyond it.
> `n-max 7` did lose to `n-max 4`, but
> **8 is this drafter's `block_size`**, its natural unit, and 4 was cutting every block in half.
>
> | Workload (single matched run each) | `n-max 4` | **`n-max 8`** | Change |
> |---|---|---|---|
> | **Pure code generation** | ~41 t/s | **~51 t/s** | **+23%** |
> | Pure code, 4 concurrent | ~60 t/s aggregate | **~77 t/s** | **+29%** |
> | Code + explanation | ~31 t/s | **~33 t/s** | **+7%** |
> | **Prose** | **~27 t/s** | ~24 t/s | **-10%** |
> | **Real agentic task** | ~26.5 t/s | ~27 t/s | no change |
>
> **Set `--spec-draft-n-max 8` on a server dedicated to code generation. Keep `n-max 4` for prose, chat and
> agentic work.** These are per-server launch flags, so both can run at once on different ports.
>
> **Two flags that do nothing, so don't bother:**
> - **`--spec-draft-p-min`** is inert on this drafter. `n8/p0.0` and `n8/p0.8` measured within 0.02 t/s of
>   each other at identical 88.9% acceptance. DFlash2 has its own confidence-based path selector, so
>   llama.cpp's floor has nothing left to cut.
> - **`n-max` above 8** buys nothing: `n8` and `n16` produced **byte-identical draft counts** (3937 drafted
>   / 3521 accepted). The `block_size` of 8 is the real ceiling, and a larger `n-max` is ignored.
>
> **Quality is unaffected**, because the full model verifies every token. **And the gain survives real
> sampling:** acceptance moved only 96.0% to 93.8% across temperature 0 to 1.0, so this is not a
> greedy-decoding figure. Run-to-run output is *not* bit-exact in either configuration (10 identical greedy
> requests gave 2 distinct outputs at `n4` and 3 at `n8`, so the drafter is the source, not the depth).
>
> **Latency, measured on the code workload:** inter-token latency was **~24 ms at `n4` against ~20 ms at
> `n8`**, about 19% faster per token, which is what the speed-up actually feels like. Prefill was ~560-585 ms
> for an 83-token prompt, so on short prompts **time-to-first-token is dominated by fixed per-request cost,
> not by prompt length.**

**Leave the KV cache at its default (f16).** The `-ctk q4_0 -ctv q4_0` advice circulating for this stack
exists to squeeze more context onto a 24 GB graphics card. **That is not the constraint here.** With a
~100 GiB unified pool, context ceiling is not what limits us; memory bandwidth is. Quantised KV is reported
to *cost* speed on this hardware rather than buy any: a third-party measurement on a different model
of this class puts it at **66.0 t/s at `q8_0` against 82.0 at f16**. *(Third-party, and not measured
by us on this stack.)*

**Use the `Q4_K_M` drafter (1.14 GB) or the `Q8_0` (2.06 GB), because they measure the same. Avoid 2-bit
and BF16.** All four available drafter quantizations, one fixed code-and-prose prompt, same stack:

| Drafter | Size | Acceptance | Generation |
|---|---|---|---|
| Q2_K | 705 MB | 44.7% | ~25 t/s |
| **Q4_K_M** | **1.14 GB** | 53.4% | **~28 t/s** |
| **Q8_0** | **2.06 GB** | 55.6% | **~28 t/s** |
| BF16 | 3.86 GB | 52.4% | ~25 t/s |

`Q4_K_M` and `Q8_0` were **not separable at n=5** (they differed by 0.30 t/s and ~2.2 acceptance points),
so prefer `Q4_K_M` for the smaller footprint. Note the repeats here are not independent, so a standard
error over them would understate the true spread; treat this as "no difference we could detect", not as a
proven equivalence. **A widely-shared claim that a 2-bit drafter holds acceptance identically does not
hold here.** It lost ~11 points of acceptance and ran ~10% slower, because the acceptance loss outweighs
the bandwidth saved. That claim came from a 24 GB graphics card where the motivation was freeing VRAM; on
this hardware, memory is not the constraint. **BF16 is the slowest of all four**, because its extra size
costs more than its precision returns.

> [!NOTE]
> **Acceptance percentages are prompt-dependent, so don't compare them across workloads.** The figures in
> the table above come from one fixed **code-and-prose** prompt, which is why they sit below
> [§6](#6-is-the-drafter-actually-working)'s **~65% code** and above its **~44% prose** rows — three
> different prompts, three different acceptance levels. A *maximally-draftable* pure-code prompt on a
> later rig measured **88.9-96%**. Compare arms only within a single matched run.

---

## 8. Known traps

| Trap | What happens | Fix |
|---|---|---|
| Sourcing the SDK at runtime | Harmless, but confusing | Only the **build** shell needs `setup-env.sh` |
| Gating startup on a log string, or on the port | A stale grep pattern reads exactly like a failed launch; the port opens minutes before the model is loaded | Gate on the `/health` **HTTP status code**, as above |
| Copying `n-max 8` onto a prose server | ~10% slower than `n-max 4` on prose | Pick `n-max` per workload; both are per-server flags |
| Passing `-np` without raising `-c` | Context is sliced per slot; long prompts return HTTP 400 | `-np N -c (16384*N)`, then read `n_ctx_seq` from the log |
| Expecting bitwise-identical output | Temp-0 output can diverge at a near-tie token | Expected; see the losslessness note above |
| Mixing drafter and target | Nonsense, or near-zero acceptance | The drafter must match the target exactly |
| Another large job resident on the node | Model fails to load, misleadingly reported as a load error | Check free GTT before launching anything large |

---

## 9. Undo

Removes everything this runbook created. Nothing outside these paths is touched.

```bash
pkill -f "llama-server.*<port>"      # ★ FIRST: a live server keeps ~18 GB of GTT reserved
rm -rf ~/dflash2
```
```bash
rm -rf ~/vulkansdk ~/vulkansdk.tar.xz
```
```bash
rm -rf ~/models/qwen38-27b
```

---

## 10. What was verified, and what was not

**Verified:** ~2.6× synthetic code generation, ~2.3× decode on real traffic, ~75% acceptance on a real
agent loop, ~1.7× wall clock on a real agentic task, the speedup holding at 13k context depth, and tool
calls plus JSON-schema output both working.

**Not measured:** a multi-hour stability soak, and a broader lossless-comparison battery across varied
prompts.

**Build note:** PR #27342 has since merged, so DFlash2 now ships in mainline llama.cpp releases and the
pinned-commit build is no longer necessary for a fresh install. The pin is kept here because it is the
build these numbers were measured on. Re-verify after moving to mainline.

---

**See also.** Acceptance and rate are prompt-dependent in ways
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) sets out, so read it
before lifting a cell off this page into anything else.
[numbers.md](../../reference/numbers.md) is where this stack sits alongside the others we have
measured, and [which-model-for-which-job.md](../../reference/which-model-for-which-job.md) is the
single-stream-versus-shared-endpoint decision in one place.
