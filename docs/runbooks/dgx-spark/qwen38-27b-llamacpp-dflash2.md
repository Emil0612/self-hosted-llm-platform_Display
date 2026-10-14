# Qwen3.8-27B + DFlash2 on llama.cpp (DGX Spark / GB10)

```text
Verified against : DGX Spark class node (GB10 Grace Blackwell, arm64, compute capability 12.1 / sm_121, 128 GB unified)
Engine / build   : llama.cpp mainline b10713, commit 557614e02 (ggml 0.22.0), CUDA 13.0.88, driver 580.173.02, GCC 13.3.0
Date verified    : 2026-09-01
```

The **no-Docker, no-sudo** way to serve Qwen3.8-27B at Q4 on a GB10 node, with the DFlash2
speculative drafter. Everything below runs as an unprivileged user in `~`.

> [!NOTE]
> **Where this sits:** on the same fixed-transcript agentic replay, the
> [SGLang + NVFP4 + DFlash2](qwen38-27b-sglang-nvfp4-dflash2.md) stack measured **~23% faster
> pooled decode** (~40-41 vs ~33 t/s) with far better TTFT (~0.14-0.41 s vs ~4.2 s on the
> big-prefill turn). This recipe's value is that it needs **no Docker, no sudo, no container
> registry**: build, pull two GGUFs, serve.

---

## 1. Build (no sudo, no curl)

```bash
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build-cuda -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=121a-real \
      -DGGML_CURL=OFF
cmake --build build-cuda -j --target llama-server llama-bench
```

**Your choice:** `~/llama.cpp` is illustrative. Clone wherever you keep source; nothing in this
recipe depends on the location.

- **`121a-real` is the architecture flag that works.** Building for `120` **fails outright with
  "no kernel image"**, because generic Blackwell PTX is not valid for GB10. The auto-detected
  value (`GGML_NATIVE`) is `121-real`, which builds but has no native FP4 path.
  ⚠ The often-quoted "`121a-real` is worth ~42%" figure is a **cross-apparatus inference and is not
  verified**, so do not repeat it. What is verified: `120` does not build, `121a-real` does.
- **`-DGGML_CURL=OFF` is deliberate.** Enabling curl pulls in `libssl-dev` and `clang`, which is the
  only step in this recipe that would need sudo. Consequence: `llama-server` has no HTTPS, which is
  a non-issue when it binds `127.0.0.1`.
- Build time: **5-10 min** on this hardware class.

## 2. Model + drafter files

| role | repo | file | size (bytes) |
|---|---|---|---|
| target | `unsloth/Qwen3.8-27B-GGUF` | `Qwen3.8-27B-UD-Q4_K_XL.gguf` | 17,923,394,624 |
| drafter | `z-lab/Qwen3.8-27B-DFlash2-GGUF` | `Qwen3.8-27B-DFlash2-Q8_0.gguf` | 2,056,414,752 |

The drafter comes from **z-lab**, not from the target's repo. Both files pull straight over HTTP
with no token, since both repositories are public:

```bash
mkdir -p ~/models/qwen38-27b && cd ~/models/qwen38-27b   # §3 serves from here

wget -O Qwen3.8-27B-UD-Q4_K_XL.gguf \
  "https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/Qwen3.8-27B-UD-Q4_K_XL.gguf"
wget -O Qwen3.8-27B-DFlash2-Q8_0.gguf \
  "https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2-GGUF/resolve/main/Qwen3.8-27B-DFlash2-Q8_0.gguf"
```

Check both with `wc -c` against the byte sizes in the table above before you trust any number you
measure. A `Q4_K_M` drafter is also published in the same repository; on a Strix Halo (gfx1151) node
the two measured the same speed, so the smaller one is a reasonable substitute. **Not measured:**
the `Q4_K_M` drafter on this hardware class.

⚠ **HF-revision trap: byte-compare before trusting any cross-machine number.** A later fresh pull
of the same repo/quant name returned **17,559,178,144 B (−364 MB)**; the drafter differed by 64 B.
A filename or repo pin is **not** identity. Rule earned: run `wc -c` on both machines' copies before
any cross-machine comparison cell.

Model pull measured at **~17 min** on a blended ~11 MiB/s link; other pulls on the same box ran much
faster. **Total bring-up ~35 min, mostly download.**

## 3. Serve

```bash
~/llama.cpp/build-cuda/bin/llama-server \
  -m ~/models/qwen38-27b/Qwen3.8-27B-UD-Q4_K_XL.gguf \
  -md ~/models/qwen38-27b/Qwen3.8-27B-DFlash2-Q8_0.gguf \
  --spec-type draft-dflash \
  --spec-draft-n-max 8 \
  -ngl 999 -ngld 999 \
  -c 40960 -np 1 -fa on --jinja \
  --alias qwen38 \
  --host 127.0.0.1 --port 8090
```

**Your choice:** `~/models/qwen38-27b/` is illustrative; point `-m` and `-md` at wherever you keep
weights.

Flag notes:

- **`--spec-type draft-dflash`**: mainline carries both `draft-dflash` and `draft-dspark`
  (upstream PR #27342). DFlash2 is the pick here. DSpark was researched and rejected for agentic
  work, since DFlash2 measured ahead by every credible published source, and edit-heavy work is
  already covered better by the [Flash-Next ngram recipe](qwen38-flash-next-180b.md).
- **`--spec-draft-n-max 8`** is the current flag name; `--draft-max` was removed upstream.
  A Strix Halo (gfx1151) node measured **n-max 8 ≈ +23% over n-max 4** on code generation, and
  **above 8 is inert on llama.cpp** because DFlash2's block size is 8. For prose/agentic-only
  mixes, n-max 4 is the conservative setting.
- `-np 1` gives a single slot. This recipe was measured single-stream; concurrency was not measured
  on this stack.
- ⚠ **Port 8090 is shared with the other two llama.cpp recipes in this folder.** Only one of the three
  can run at a time, the second fails to bind *after* loading its weights, and the teardown `pkill`
  below matches whichever is running. **Give each a different `--port` if you want two at once.**
- `--alias qwen38` sets the served model id. Matching it to the id the SGLang recipe serves lets
  clients and replay bodies move between the two stacks unmodified.

## 4. Readiness gate: HTTP status, never the port

> [!WARNING]
> **`llama-server` binds its listening port BEFORE the model finishes loading.** An `ss -tln` gate
> fires minutes early and the first request comes back
> `{"error":{"code":503,"message":"Loading model"}}`.

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/health)" = "200" ]; do
  sleep 5
done
```

Gate on the **status code**, not on the port and not on a body grep. The same rule holds across
engines here: SGLang's `/health` returns 200 with an empty body, so a body grep would fail on a
healthy server.

## 5. Measured performance

Instrument: **fixed-transcript agentic replay**, meaning one real captured `opencode` session
(5 turns of verbatim `/v1/chat/completions` bodies) replayed identically, warm-up discarded, n=3.
Pooled decode = all generated tokens ÷ all decode seconds.

| workload | measured |
|---|---|
| Agentic replay, pooled decode | **~33 t/s** (33.07 pooled; per-run weighted 29.4-36.7) |
| Pure code generation, single-stream | **~41.5 t/s**, draft acceptance **64.6%** (208/322) |
| Bare decode, no drafter (`llama-bench` tg128) | ~12.2 t/s |
| TTFT, first (shallow) turn | 1.47-1.48 s |
| TTFT, large-prefill turn | **~4.2 s** (4.22-4.25 s) |
| Serving prefill | not measured on this stack (bench pp512 ~835 t/s) |

- Speculation is the whole story: **~41.5 vs ~12.2 bare = ~3.4×**. This was the first measurement
  known to us of DFlash2 running on CUDA `sm_121`.
- ⚠ **The replay instrument carries a ±10% day/composition band.** Identical config pooled 40.77 one
  day and 35.5-37.7 the next on the SGLang stack; the same caution applies here. Quote ranges with
  the workload named, and require **same-day interleaved controls** for any sub-10% claim.
- Acceptance is workload-dependent: ~65% on pure code, but real agentic content (with reasoning
  turns) runs materially lower, which is why agentic pooled rate sits well below the code cell.

## 6. Verified behaviours worth knowing

- The full free-running agent client (`opencode`, linux-arm64) installs and completes the task
  end-to-end on this hardware (5/5 plain-assert tests). Client overhead is not a lever; engine and
  quant are.
- `llama-bench` numbers are **not** serving numbers: no HTTP, no chat template, no sampler. Our own
  records show `llama-bench` overstating served throughput by ~6.6%.
- Widely published llama.cpp Spark figures build with `-DGGML_CUDA=ON` and **no architecture flag at
  all**; those numbers sit far below what this build line produces. Do not import them.

---

## 7. Teardown

```bash
pkill -f "llama-server.*8090"
```

Models and the build tree are the only artefacts; removing `~/llama.cpp/build-cuda` and the GGUFs
returns all disk.

---

**See also.** The instruments, the day band and the rules for quoting any of the cells above are
written down once in
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md).
[numbers.md](../../reference/numbers.md) puts this stack next to every other one we have measured,
and [which-model-for-which-job.md](../../reference/which-model-for-which-job.md) is the page to read
if you are still choosing between them.
