# ROCmFPX + ROCmI4 + embedded MTP for Qwen3.8-27B

```text
Verified against : AMD Strix Halo (gfx1151) node, 128 GB unified memory
Engine / build   : ROCmFPX fork of llama.cpp, commit c49ebdbd5c9f01ec242369f9e7f7967855f80cba
System ROCm      : 7.2.4 (the fork's own ROCm 7.14 toolchain script was NOT needed)
Date verified    : 2026-08-25
```

> [!IMPORTANT]
> **Headline finding: this build works, and it measured slower than
> [llama.cpp + Vulkan + DFlash2](qwen38-27b-llamacpp-vulkan-dflash2.md) on every controlled workload we
> ran, by roughly 10-20%.** The recipe is kept because the build is non-obvious and the evaluation should
> be repeatable if the upstream project improves. If the goal is serving Qwen3.8-27B fast on this hardware,
> the DFlash2 recipe is the faster measured path.

## 1. What this is

A third-party int4 build of Qwen3.8-27B for Strix Halo specifically:

- **ROCmI4** is signed 4-bit weights packed two per byte with block scales, using the gfx1151 native
  `v_wmma_i32_16x16x16_iu4` integer matrix instruction. The file is **14.5 GB** against the usual 4-bit
  K-quant build's 17.9 GB.
- **Embedded MTP head** means the NextN multi-token-prediction head ships *inside* the GGUF, so
  speculative decoding needs **no separate drafter model**.
- **W4A4** (an optional build flag) additionally quantises *activations* to 4 bits.

It requires a fork. Stock llama.cpp will not load the file.

## 2. What we measured

Same node, same day, same harness, one request at a time, 512-token generations, temperature 0. Values are
single matched-run figures, so compare arms within this table, not against numbers from another session.

| Workload | ROCmI4 + MTP *(their settings)* | ROCmI4 + MTP *(tuned by us)* | DFlash2 *(as shipped)* | DFlash2 *(tuned)* |
|---|---|---|---|---|
| Pure code generation | ~34 t/s | **~40 t/s** | **~41 t/s** | **~51 t/s** |
| Code + prose | ~28 t/s | not measured | **~31 t/s** | **~33 t/s** |
| Prose | ~24 t/s | not measured | **~27 t/s** | ~24 t/s |
| Bare (no speculation) | **~14 t/s** | not measured | ~12 t/s † | not measured |

† **The one cell not from that day.** The DFlash2 bare-decode figure is carried over from the previous
session; every other cell in the table is same node, same day, same harness. Do not treat it as a
same-day matched arm.

**DFlash2 is faster on every workload, by roughly 10-20%.** ROCmI4's one genuine advantage is bare decode,
where its smaller file streams faster, and speculation gives that back.

> [!NOTE]
> **The upstream numbers reconcile with ours.** Their published 40-50 t/s comes from HumanEval, and their own
> live-service figure is 29-43 t/s, which is exactly the band our measurements of their build landed in.
> We reproduced their non-speculative baseline (13.8 claimed, 13.88 measured, 0.6% apart). Their headline
> 49.4 is a 10-task pilot; the full 164-task mean is 44.4.

**Quality:** it completed a real agentic coding task correctly, writing a CLI tool, writing tests, running
them and fixing until they passed. Their published perplexity is +6.1% against a full-precision reference,
and W4A4 adds a further ~5.4%.

### Where its performance actually comes from

The comparison above changes two things at once, the quantization format **and** the drafter, so we
isolated them. The `UD-Q4_K_XL` K-quant file turns out to contain an **unused NextN/MTP head**, which let
us run the same drafter *type* on the same weights, changing nothing else:

| Same file (`UD-Q4_K_XL`), drafter varied | t/s | Acceptance | Inter-token latency |
|---|---|---|---|
| DFlash2 external drafter, `n-max 8` | **~51** | 88.9% | **~20 ms** |
| DFlash2 external drafter, `n-max 4` | ~41 | 96.0% | ~24 ms |
| **Embedded MTP head**, `n8/p0.8` | **~32** | 89.2% | ~31 ms |

**So the MTP head is the weaker drafter by 30-61%, on identical weights.** ROCmI4's ~40 t/s comes mostly
from its **smaller file** (14.5 GB against 17.9 GB, giving bare decode ~13.9 against ~12.2 t/s), not from
a better drafter. Scaling our MTP figure by that size ratio predicts ~36 against their measured ~40, so
roughly **11% remains genuinely theirs**: a faster int4 kernel, or a better-trained head. Real, but not
enough to change the ordering.

> [!NOTE]
> **`--spec-draft-p-min` matters here and does not on DFlash2**, which is worth knowing if you tune either.
> On this MTP head, `p0.60` to `p0.8` at `n-max 16` was worth **about +10%** on pure code generation (~34
> to ~38 t/s), because an autoregressive head pays for every drafted token, so truncating early pays.
> DFlash2 ignores the flag entirely because it already truncates on its own confidence internally.

> [!NOTE]
> **Temperature does not hurt either drafter.** Acceptance moved 89.2% to 86.5% on this MTP head and 96.0%
> to 93.8% on DFlash2 across temperature 0 to 1.0. A third-party report on identical hardware claimed 88%
> falling to 25% at temperature 0.8; **we could not reproduce that on either drafter** and have no
> explanation for the difference.

> [!IMPORTANT]
> **One result contradicts the ordering above and is unresolved.** On a *single* real agent-loop run
> (thinking on, tool calls), ROCmI4 was **~16% faster on decode with 82.9% draft acceptance**, the highest
> of the three agent-loop arms — **not of this page**: the same night's prompt sweeps reached 83.8%,
> 88.9%, 94.7%, 96.0% and 96.3%, so this is not a record of any kind. Its autoregressive MTP head appears to draft reasoning traces unusually well. That
> run is n=1 and confounded by trajectory variance, so it does not overturn the controlled results, but it
> is a reason to re-test rather than close the file.

---

## 3. Build

Requires `git`, `cmake`, `hipcc` and ~10 GB disk for the build tree. No sudo.

```bash
git clone https://github.com/charlie12345/ROCmFPX.git ~/rocmfpx
cd ~/rocmfpx
git checkout c49ebdbd5c9f01ec242369f9e7f7967855f80cba
git rev-parse --short HEAD          # must print c49ebdb
```

The fork URL and the commit hash in that block are the values recorded at the time of the trial.
**Confirm upstream:** check the project for a newer commit before following this step. The pin exists
so the numbers in this runbook stay reproducible, not because it is the version you ought to run.

> [!WARNING]
> **The ROCm linker workaround is mandatory on this hardware.** ROCm's `lld` is built against
> `libxml2.so.2`, which Ubuntu 26.04 does not ship. Without the symlink the build fails with a misleading
> `unable to execute command: No such file or directory`. It is a build-time need only, and the finished
> binaries do not depend on it.

```bash
mkdir -p ~/lldcompat
ln -sf /usr/lib/x86_64-linux-gnu/libxml2.so.16 ~/lldcompat/libxml2.so.2
export LD_LIBRARY_PATH=$HOME/lldcompat:$LD_LIBRARY_PATH
```

```bash
cmake -S . -B build-rocmi4 \
  -DGGML_HIP=ON \
  -DGGML_VULKAN=OFF \
  -DCMAKE_HIP_ARCHITECTURES=gfx1151 \
  -DGGML_HIP_ROCMI4_W4A4=OFF \
  -DLLAMA_BUILD_SERVER=ON

cmake --build build-rocmi4 \
  --target llama-server llama-cli llama-bench llama-perplexity -j 16
```

Gate on the binaries existing, not on the log. **Not recorded: how long the build took on our node.**

> [!NOTE]
> **Build HIP-only.** A combined HIP/Vulkan scheduler can route ROCmI4 tensors to Vulkan, which has no
> shader for this custom type.
>
> **`llama-perplexity` is not in the upstream target list.** Add it as above, or you will rebuild later
> when you want a quality screen.
>
> **The fork ships a script that downloads a ROCm 7.14 toolchain.** We did **not** need it; the build
> succeeded against system ROCm 7.2.4.

### The W4A4 variant: measure before you assume it helps

Build into a **separate** directory with `-DGGML_HIP_ROCMI4_W4A4=ON`. Name it `build-rocmi4-w4a4`,
inside the same clone, so it sits beside `build-rocmi4` and the teardown below removes both:

```bash
cd ~/rocmfpx
cmake -S . -B build-rocmi4-w4a4 \
  -DGGML_HIP=ON \
  -DGGML_VULKAN=OFF \
  -DCMAKE_HIP_ARCHITECTURES=gfx1151 \
  -DGGML_HIP_ROCMI4_W4A4=ON \
  -DLLAMA_BUILD_SERVER=ON

cmake --build build-rocmi4-w4a4 \
  --target llama-server llama-cli llama-bench llama-perplexity -j 16
```

> [!WARNING]
> **W4A4 is not a general speedup, despite the upstream +18.7% figure.** We measured **about +7% on pure
> code generation and about -9% on code+prose.** It buys kernel speed and pays for it in draft acceptance
> (down 2.5 and 6.0 points), because quantising activations perturbs the model's own distribution and the
> draft head then agrees with the target less often. Where acceptance has headroom the kernel gain wins; on
> mixed content the acceptance loss dominates and the model gets **slower**. It also costs a further ~5.4%
> perplexity by the author's own measurement.

---

## 4. Download the model

```bash
mkdir -p ~/models/qwen38-rocmi4 && cd ~/models/qwen38-rocmi4
B=https://huggingface.co/cafonez/Qwen3.8-27B-ROCmI4-MTP-GGUF/resolve/main
wget -q $B/checksums.sha256 $B/launch-rocmi4-mtp.sh
wget -c $B/Qwen3.8-27B-Q4_0_ROCMI4.gguf     # 14.5 GB
sha256sum -c --ignore-missing checksums.sha256
```

The repo ships checksums, so verify rather than eyeballing a file size. `mmproj-BF16.gguf` is only
needed for vision; skip it for text serving.

---

## 5. Serve

```bash
~/rocmfpx/build-rocmi4/bin/llama-server \
  -m ~/models/qwen38-rocmi4/Qwen3.8-27B-Q4_0_ROCMI4.gguf \
  --host 127.0.0.1 --port <port> \
  -dev ROCm0 -ngl 999 -np 1 -c 16384 \
  -b 512 -ub 256 -t 16 -tb 32 -fa on \
  -ctk f16 -ctv f16 --jinja \
  --spec-type draft-mtp --spec-mtp-strict-qwen \
  --spec-draft-device ROCm0 --spec-draft-ngl all \
  --spec-draft-type-k f16 --spec-draft-type-v f16 \
  --spec-draft-n-max 8 --spec-draft-n-min 0 \
  --spec-draft-p-min 0.80 --spec-draft-backend-sampling
```

Gate readiness on the **HTTP status code**, never on the port and never on a log string.
`llama-server` binds its port before the model loads and answers 503 until ready, and ready-message
wording changes between builds:

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' 127.0.0.1:<port>/health)" = "200" ]; do sleep 5; done; echo SERVER-UP
```

> [!IMPORTANT]
> **`--spec-draft-n-max 8 --spec-draft-p-min 0.80` is our tuning, and it measured about 18% better than the
> settings on the model card** (`n-max 16 / p-min 0.60`). Running the card's own recommendation is the
> largest single reason a measurement of this build falls short of the published numbers.

Sweep on pure code generation, showing why:

| Setting | drafted/pass | acceptance | t/s |
|---|---|---|---|
| `n16 / p0.60` *(model card)* | 8.95 | 59.4% | ~34 |
| `n16 / p0.8` | 6.57 | 72.3% | ~38 |
| **`n8 / p0.8`** | 5.38 | 83.8% | **~40** |
| `n4 / p0.8` | 3.28 | **96.3%** | ~39 |

> [!NOTE]
> **Do not tune for acceptance percentage.** The 96.3% arm is slower than the 83.8% one. Acceptance measures
> *how much drafting effort was saved*, not output quality, since the full model verifies every token either
> way. When draft depth varies, the number that tracks throughput is **accepted tokens per target pass**.
>
> **Why this drafter wants shallow drafts.** The MTP head is *autoregressive*, so drafting n tokens costs n
> sequential head evaluations, measured at **~14 ms each** across four independent configurations. DFlash2's
> block-diffusion drafter produces its whole block in one pass, so it wants the *opposite* tuning: deeper
> drafts, higher `n-max`. Same flags, opposite optimal direction.

---

## 6. Known traps

- **Thinking is ON by default.** A small `max_tokens` is consumed entirely by reasoning and returns empty
  content. Suppress per request with `"chat_template_kwargs":{"enable_thinking":false}`, or raise the budget.
- **`dflash requires ctx_other to be set`** at startup is benign, and normal during memory fitting.
- **`model has unused tensor blk.64.nextn.*`** means the MTP head was found but is not the active drafter
  for that configuration.
- **`HSA_OVERRIDE_GFX_VERSION=11.5.1`** is documented upstream for systems where ROCm doesn't recognise
  Strix Halo. **We did not need it.**
- **Prompt-cache reuse can be rejected** under speculation: `prompt cache skip:
  reason=spec-boundary-mismatch`. The effect on real multi-turn traffic was not measured; it is relevant if
  prefix reuse matters to your workload.
- **A generation benchmark cannot see a correctness bug.** Everything above is throughput; the agentic-task
  completion is the only correctness evidence in this file. See the
  [hardware overview](README.md#5-a-generation-benchmark-is-blind-to-correctness-and-to-serving-overhead).

---

## 7. Teardown

Removes everything this runbook created. Nothing outside these paths is touched.

```bash
pkill -f "llama-server.*<port>"
```
```bash
rm -rf ~/rocmfpx                 # the clone, and with it build-rocmi4 and build-rocmi4-w4a4
```
```bash
rm -rf ~/models/qwen38-rocmi4    # the model and its checksums
```
```bash
rm -rf ~/lldcompat               # the build-time libxml2 symlink
```

Nothing was installed system-wide: no systemd unit, no packages, no sudo at any step.

---

**See also.** This page records an evaluation rather than a recommended stack, so the rules for
comparing arms matter more than usual here:
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) has them.
[numbers.md](../../reference/numbers.md) carries the cross-stack table these cells belong to, and
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md) points at what to run
on this node instead.
