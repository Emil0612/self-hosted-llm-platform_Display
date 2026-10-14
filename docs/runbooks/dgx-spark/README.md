# DGX Spark (GB10) runbooks

Recipes for standing up inference on a **DGX Spark class node** (NVIDIA GB10 Grace Blackwell, sold
both as the NVIDIA DGX Spark and as OEM variants such as the ASUS Ascent GX10). Everything here was
verified on a unit of this class in our own deployment; nothing is imported from vendor or third-party
benchmarks unless it is explicitly labelled as such.

**Placeholder convention.** Anything in `<angle brackets>` is a value you must supply, and a short
bolded note directly under the code block says which kind it is:

| note | meaning |
|---|---|
| **Confirm on the node:** | a real value exists but we never recorded it. Read it off a running node with the command given, then write it back into the recipe. |
| **Confirm upstream:** | reconstructed from a description, not from a recorded command. Check it against the real upstream repository before following the step. |
| **Your choice:** | an operator decision (a directory, an alias). Nothing is missing; pick what suits you. |

Every runbook in this folder opens with a verification header:

```text
Verified against : <hardware class>
Engine / build   : <version / commit / image digest>
Date verified    : <YYYY-MM-DD>
```

If you follow a runbook and something has moved, update the header and the step in the same pass.

---

## What the hardware is: vendor figures unless marked measured

| component | detail |
|---|---|
| SoC | NVIDIA **GB10 Grace Blackwell** superchip |
| CPU | 20 Arm cores: 10× Cortex-X925 (the fast cluster) plus 10× Cortex-A725 |
| GPU | Blackwell, 48 SM / 6,144 CUDA cores, **compute capability 12.1 (`sm_121`)** |
| Memory | **128 GB LPDDR5X unified**, vendor spec 273 GB/s; ~121 GiB usable to the OS, ~124,500 MiB addressable as VRAM |
| Storage | 1 TB NVMe |
| Network | 10GbE plus ConnectX-7 (200GbE RDMA, **hot-plug**, see below) |
| Compute | ~1 PFLOP FP4 class; **NVFP4 supported in hardware** |
| OS as verified | Ubuntu 24.04.4, kernel 6.17.0-1031-nvidia, driver 580.173.02, CUDA 13.0.88 |

### ⚠ It is arm64-only

Every container image, Python wheel and driver must be **aarch64**. x86 images do not run at all.
This is the single most common reason a recipe that works elsewhere does not work here, so check
the architecture of an image before you debug anything else. `sm_121` is also distinct from
discrete Blackwell (`sm_120`): building CUDA code for `120` fails outright with "no kernel image is
available", and a generic Blackwell build emits wrong PTX. Where a build flag is available, target
`121a-real` (the `a` enables native FP4).

---

## The measured truth: decode is a tie, prefill is the win

This is the finding that should shape what you put on this box. Measured by us against a Strix Halo
(gfx1151) node.

⚠ **Read the apparatus before the ratio. Neither pair below is a single-variable comparison.** The
gpt-oss rows put llama.cpp **Vulkan** on the AMD side against llama.cpp **CUDA** on this one — same
project, different backend and different build. The agentic rows come from a **different campaign on
a different day**, with each box running its own fastest configuration. These are box-versus-box
comparisons by design, which is the useful question when deciding what to run where, but they are
not silicon-versus-silicon and no row here isolates the hardware.

| workload | Strix Halo (gfx1151) | DGX Spark (GB10) | ratio |
|---|---|---|---|
| decode, gpt-oss-20b MXFP4, `llama-bench tg128` | ~81 t/s | ~87 t/s | **+7%** |
| decode, Qwen3.8-27B Q4, bare | ~12.1-12.2 t/s | ~12.1-12.2 t/s | **a tie, see the caveat below** |
| prefill, gpt-oss-20b `pp2048` | ~2,067 t/s | ~4,758 t/s | **2.30×** |
| real agentic replay, spec-decode, decode rate | ~27 t/s | ~33 t/s | **+21%** |
| real agentic replay, TTFT on the big-prefill turn | ~10.6 s | ~4.2 s | **2.51×** |

> [!WARNING]
> **The Qwen3.8-27B row is a tie, not a precision measurement.** Both boxes land at roughly
> 12.1-12.2 t/s on bare decode, and that is as far as the cell goes. The two machines' model files
> were later found to be **different revisions of the same quant** (17,559,178,144 B against
> 17,923,394,624 B, a 364 MB difference under the same repository and filename), so no percentage
> figure can be read off this comparison. Treat it as "both land at about 12.1-12.2". The lesson and
> the `wc -c` check that catches it are in
> [adding-another-node.md](adding-another-node.md).

> [!NOTE]
> **The two Strix Halo gpt-oss-20b cells were measured on an unmerged fork build**, not on the stock
> build this repo recommends. The stock build's `tg128` the same day was 81.11 t/s, so the decode
> ratio above is unaffected; the **~2,067 t/s prefill cell is not a stock-build number** and should
> not be quoted as one.

**Do not pitch this box as a decode upgrade.** Bare token generation is bandwidth-bound, and on
bandwidth these two machines are peers. Where it pulls ahead is anything that shifts work toward
compute: prefill, long context, and (the case that matters most for us) **real agentic loops**,
where it wins on both axes in the table above.

The gap widens again once you change the stack rather than just the box. The SGLang + NVFP4 +
DFlash2 recipe in this folder measures **~35-41 t/s on agentic replay** with **TTFT 0.14-0.41 s on
every turn** (radix prefix cache), versus ~10.6 s TTFT for the same turn on a Strix Halo node. For an
interactive agent loop that is a latency-class change, not a percentage.

⚠ **Published third-party figures for this box sit far below what our own build line produces**, and
we have not isolated why. **Do not import third-party numbers for this hardware class — measure on
your own unit.**

---

## Thermals and clocks: characterise before you benchmark

Thermal throttling on this hardware class is a documented, partly-unresolved community problem
(reports of units capping well below the 240 W module rating, ACPI zones at 96-97 °C with fans not
ramping, spontaneous reboots under sustained load). Behaviour varies by unit, firmware and
workload, so **characterise your own unit before any benchmark**. A throttled box silently halves
every number and you will spend days chasing software ghosts.

**What healthy looked like on our unit**, across two independent sustained-load sessions
(12m43s of continuous inference at bring-up; 40 min / 480 samples during the measured concurrency
ladder — the 1, 4 and 8 concurrent-session run in the SGLang recipe):

- **SM clocks held 2385-2418 MHz** against a 3003 MHz ceiling, under 1% variation, no sag.
- **`clocks_event_reasons.active` = `0x0` on every loaded sample** (480/480 in the ladder run).
- Temperature ramped from ~36 °C and **plateaued at 70-73 °C**.
- Zero microseconds of SW or HW thermal slowdown logged since boot; no spontaneous reboots.

### ⚠ The power reading is not trustworthy on this hardware

`nvidia-smi` on GB10 reports **no power limit at all** (Current / Requested / Default / Min / Max
all `N/A`, Power Samples "Not Found"), and **hwmon exposes no power sensor**: only `acpitz`, `nvme`
and the wifi PHY appear. There is no `tegrastats` and no `nvpmodel`. The same GPU under comparable
sustained load read **40.0-49.5 W** at bring-up and a maximum of **9.69 W** during the ladder run.
That spread is an instrument artifact, not a degraded box.

⇒ **The throttle check must be behavioural**: sustained load, watching `clocks.sm` against
`clocks.max.sm` plus `clocks_event_reasons.active`. Do not chase the watt number.

Two further notes:

- `SW Power Cap` (`0x4`) flickering at idle in P8 is ordinary power management, not the defect.
- **A short benchmark is not a thermal test.** Our first attempt was under 3 seconds of GPU time
  and peaked at 13.42 W, so it proved nothing. Run minutes, not seconds.

> **Not measured: the 240 W envelope.** The GPU domain drew tens of watts under bandwidth-bound
> decode, which never demands the full module rating. A compute-saturating characterisation (the
> load a fine-tune produces) has **not** been run on our unit.

### ⚠ Hard power-off, cause unexplained

Separate from thermals, and worth knowing before you script any model swap. **We observed one unit
cut its own power** — unreachable, SSH dead, recovered only by a physical power cycle — immediately
after a service stop released roughly 60 GiB of GPU memory. Nothing was loading at the time.

**The cause is unexplained and we derive no rule from it.** The previous boot's journal showed zero
OOM kills, zero hung tasks and zero thermal, XID, panic or oops lines, with ordinary activity right
up to the last entry — **software failures leave a trace; this left none**, which places the event
beneath the operating system. It matches a hard-poweroff fault documented for this hardware class,
attributed to an embedded-controller cut faster than Linux can log. The timing tempts a
large-memory-transition explanation, but a community thread that checked exactly that found no
correlation, and the vendor's acknowledged trigger is **sustained GPU workload**. Our event matches
neither pattern.

If it happens to you, this is the check — **the tail matters**: `--since` is relative to now, not to
the end of the dead boot, so a time window returns nothing once you have walked to the machine and
power-cycled it. **Expect 0.** A non-zero count means the OS did leave a trace, which points
somewhere other than this fault.

```bash
journalctl -b -1 | tail -n 2000 | grep -ci "oom-kill\|hung task\|thermal\|panic\|xid"
```

The mitigation reported by the community is a GPU clock lock (`nvidia-smi -lgc 300,2200`), costing
roughly 5% throughput. **We chose not to adopt it**: the
cost is certain, and the fault has not recurred through a full day of deep-prefill work out to 248k
context.

Two habits stand on their own, whatever the root cause:

- **Gate a shutdown on the port and on GPU memory being released — never on process count.** A
  container leaves `docker ps` *before* the kernel returns its memory, so a `docker ps` gate fires
  early.
- **Leave services enabled at boot.** On our unit recovery was complete and automatic because the
  units were enabled; a disabled unit means a power event turns into an outage nobody notices.

### ConnectX-7 is hot-plug: absence in `lspci` proves nothing

The CX-7 NIC does **not enumerate in `lspci` until a QSFP cable is inserted**, so an empty `lspci`
is not evidence the NIC is missing. Confirm from `dmesg` instead (`mlx5_core` across four PCI
functions, followed by a cable-removal line) and from the presence of
`/etc/nvidia/cx7-hotplug-enabled`. Removing that file and rebooting forces the NIC permanently
visible, at the cost of roughly doubling idle power.

**No cable is required for independent serving.** See
[adding-another-node.md](adding-another-node.md).

---

## Recipes in this folder

| Recipe | Stack | Headline numbers (workload named) |
|---|---|---|
| [qwen38-27b-sglang-nvfp4-dflash2.md](qwen38-27b-sglang-nvfp4-dflash2.md) | SGLang + Qwen3.8-27B NVFP4 + DFlash2 | **~35-41 t/s agentic replay, 42-50 code, ~25 prose, TTFT 0.14-0.41 s warm**; **at ~6.5k context** 8 concurrent sessions at ~24-27 t/s each, ~162 t/s aggregate steady-state. Depth is not measured on this stack — see the row below for what it does to the comparable one |
| [qwen38-27b-llamacpp-dflash2.md](qwen38-27b-llamacpp-dflash2.md) | llama.cpp + Qwen3.8-27B Q4 + DFlash2 | ~33 t/s agentic replay, ~41.5 t/s pure code single-stream, acceptance ~64.6%. No Docker, no sudo. First measurement known to us of DFlash2 on `sm_121` |
| [gemma4-26b-llamacpp-mtp.md](gemma4-26b-llamacpp-mtp.md) | llama.cpp + Gemma 4 26B QAT q4_0 + MTP drafter | ~81 t/s agentic replay thinking off, ~87 t/s thinking on. Highest agentic rate we have measured on this box, on a smaller and less capable model than Qwen3.8, so read it as a capability number rather than an A/B |
| [qwen38-flash-next-180b.md](qwen38-flash-next-180b.md) | llama.cpp + Qwen3.8-Flash-Next 180B (GGUF Q4) | ~22-26 t/s on fresh content, ~82 t/s on file edits and rewrites, ~104 GiB on disk (111 GB by the publisher's decimal count). Served at 40k context here; the published 128k figure was **not** re-verified by us, so see the vLLM + NVFP4 row below for measured long context. A rewrite and long-context engine, not an agent driver (−22% versus the 27B + DFlash2 stack on the same replay) |
| [qwen38-flash-next-180b-vllm-nvfp4.md](qwen38-flash-next-180b-vllm-nvfp4.md) | vLLM + Qwen3.8-Flash-Next 180B NVFP4, PLE table on NVMe | **~33-36 t/s decode, flat from 8k to 128k**; ~2,100 t/s prefill, linear; **retrieval verified correct at 247,899 tokens**, and 8/8 correct under load at ~33k on a separate single-turn probe (seven of eight clients admitted, against eight for the multi-turn ladder). **Concurrency depends on depth: eight fit at ~33k**, only 5-7 are admitted past ~35k, and at ~6.5k it stops gaining past four. One 128k request costs the other users over half their speed, and the worst-hit ~80%. The long-context choice; ~135 GB on disk |
| [adding-another-node.md](adding-another-node.md) | Bring-up of a second unit to the same standard | No interconnect needed for independent serving. **Third-party, not ours:** two independent nodes beat a tensor-parallel pair from about 8 concurrent sessions upward. We have not run a pair of units in either mode |

### Approaches evaluated and rejected on this hardware class

Recorded so nobody spends a day re-deriving them. **Some were measured here and some were closed on
published evidence without a run; each line says which**, and the receipts are in
[tested-and-rejected.md](../../reference/tested-and-rejected.md):

- **DSpark** speculative decoding — *closed on published evidence, never run here*: it loses to
  DFlash2 by every credible source we could find, and the one widely-circulated cell that favours it
  is a misconfigured arm, refuted by our own measured band.
- **FreeToken** — *closed on research, never built or run here*: its offload mechanism is moot to
  actively harmful on unified memory, and its **published** fused-mode figure sits well below the
  llama.cpp rate we measured ourselves on the same model.
- **Draft-budget sweeps** 10 / 12 / 16 on SGLang: flat on agentic content, budget 8 stays.
- **Calibrated NVFP4 drafter quantization**: a dead tie, since bandwidth saved is acceptance lost.
- **Docker cpuset pinning** to the fast core cluster: inside the day band, not adopted. Note that
  `--cpuset-cpus ""` silently no-ops; the only trustworthy undo is an explicit `"0-19"`.
- **fp8 KV cache** — *closed on upstream evidence, never run here*: structurally incompatible with
  DFlash2, and it produces infinite repetition. KV stays bf16. This one has **no entry in
  `tested-and-rejected.md`**; its receipt is the second hard-limit warning in
  [qwen38-27b-sglang-nvfp4-dflash2.md](qwen38-27b-sglang-nvfp4-dflash2.md#2-the-pinned-stack).

---

## Reading the numbers in these recipes

Read [how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) before quoting
anything here. Three things bite hardest on this hardware class: the agentic replay instrument
carries a **±10% day-and-composition band**, so quote ranges and never a flat point value;
**acceptance belongs beside every rate**, or two sessions are not comparable; and **reasoning effort
is a 27B lever that does not transfer to the 180B model**
([numbers.md §4](../../reference/numbers.md#4-reasoning-effort-an-agent-loop-lever-not-a-per-request-one)).
