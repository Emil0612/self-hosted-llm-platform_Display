# Tested and rejected

Things we evaluated and did **not** adopt, with the reason and the receipt, so nobody spends a day
re-running them.

Each entry says what was claimed, what we actually measured (or what evidence closed it without a
run), and the verdict. Where a result is narrow, true on one hardware class but not another, that
is stated explicitly, because the most expensive mistake in this list would be carrying a verdict
across a boundary it does not cross.

---

## 1. DSpark speculative decoding: closed on published evidence, no run

**What it was:** **DSpark**, a second speculative-decoding algorithm shipping alongside DFlash2 in
the same engines (mainline llama.cpp carries both `--spec-type draft-dflash` and `draft-dspark`),
named for and tuned to the DGX Spark class, offered as an alternative to the block-diffusion DFlash2
drafter we run.

**Why we did not run it:** DSpark loses to DFlash2 by every credible source we could find.

- An independent GB10 + NVFP4 measurement: DFlash2 **+43% over DSpark** on single-stream.
- An independent FP8 benchmark at the same draft depth: DFlash2 **1.66×** DSpark's throughput.
- Lineage: DSpark beat the *first* generation DFlash by +13.9%, and DFlash2 then leapfrogged both.
  Both are lossless on a maths benchmark, so there is no quality argument either way.

**DSpark's one strong cell** is edit-heavy work with thinking off (~75-78 t/s reported). With
thinking on it measures 16-24 t/s, below our own 35-41 t/s band, and acceptance drops to 18% on
fresh prompts. We already own edit-heavy work better, with a different model measured at ~82 t/s on
exactly that shape.

⚠ One widely circulated cell claiming DFlash2 runs at 13 t/s is a misconfigured arm (wrong draft
depth plus a streaming harness that costs 15-25%). It is refuted by our own measurements and by the
best published figure for our stack. Do not import it.

**Verdict: DSpark rejected without a run. The published evidence was enough to close it.** Do
not worth re-testing for agentic work.

---

## 2. FreeToken: the headline mechanism is actively harmful here

**What it claims:** **FreeToken** is an edge-native mixture-of-experts serving engine that schedules
expert weights between fast device memory and slower system memory so larger models fit on smaller
machines.

**Why it does not apply:** both our hardware classes have **unified memory**. There is no fast tier
and slow tier to schedule between, so the mechanism has nothing to do. Worse than moot, **on the
published figures** — we never built or ran FreeToken, so nothing below is our measurement:

- **Offload mode: 126-141 seconds for a trivial probe, versus 0.22-0.77 seconds in fused mode.**
  Hundreds of times slower. Neither figure is ours, and neither is independently sourced here; what
  the pair supports is the **order of magnitude**, not the numbers. The signature feature has to be
  turned off for the tool to be usable at all.
- **Fused mode** (i.e. the feature disabled): the published GB10 figure for gpt-oss-20b is
  **37.8-47.1 t/s**, against the **87.09 t/s we measured on the same model with llama.cpp**
  (`llama-bench tg128`). Roughly half our number, and ours is the only measured cell here.

Two further checks closed it: FreeToken's own roadmap lists a unified-memory mode as *planned*,
with no design and no dates, an implicit admission that the current mechanism does not fit this
hardware. And its runnability on our exact chip could not be re-confirmed: a claim of verified
community builds did not survive a re-check on 2026-09-01, so treat GB10 runnability as unverified.

The related idea of fitting a ~400 GB model on one box by this route is separately closed, again on
published work rather than a run of our own: a study of **Pulsar**, a comparable offload runtime,
reports ~2.1 t/s at 211 GB and ~1.1 t/s at 434 GB on this hardware class. We did not reproduce those
either, and again the finding is the order of magnitude: **staying inside the box's real memory is
worth something like 15-40×.**

**Verdict: FreeToken rejected on research, without a run. Re-open only if a genuine unified-memory
mode ships.**

---

## 3. DFlash2 draft-budget sweep on SGLang (8 vs 12 vs 16): flat, the default stands

**What was claimed:** raising the draft budget from 8 to 12 is worth +21% (53.9 → 65.0 t/s, n=10,
published, on coding content).

**What we measured** on our own agentic replay, changing **only** the budget in the service
definition and leaving memory fraction and container limits untouched:

| Draft budget | Pooled t/s, agentic replay (n=3) |
|---|---|
| **8 (our default)** | **40.77** |
| 12 | 39.28 |
| 16 | 40.42 |

Spreads fully overlap. **The lever is flat on agentic content.**

**Mechanism, visible in the runs:** the published +21% is a pure-coding result. Long thinking turns
carry ~80% of the tokens in real agentic work and behave like the source's own chat cell, where
budget 16 measured **−11.8%**. At budget 12 short copy-ish turns hit 76-77 t/s and deep budgets took
copy-ish turns to 84-102, but thinking turns pin around 38-41 and dominate the weighted average.

**Verdict: null. Budget 8 stays.** Not worth re-testing 10, 12 or 16. Service was restored to stock
after each arm.

---

## 4. A calibrated NVFP4 DFlash2 drafter: dead tie

**What was claimed:** quantizing the *DFlash2 drafter* to NVFP4 with proper calibration shrinks the
draft pass from ~3.5 GB to ~1.4 GB, worth +8-17%. (Naive drafter quantization is known to hurt;
calibration was supposed to be the difference.)

**What we measured:** the RTN-calibrated NVFP4 drafter checkpoint
(`maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`) plus SGLang's drafter-quantization flag. Both work
on `sm_121`, the drafter loaded and graphs captured, so this is a real arm and not a failed launch.

| Arm | Pooled t/s, agentic replay (n=3) |
|---|---|
| Stock DFlash2 drafter | 40.77 |
| Calibrated NVFP4 DFlash2 drafter | **40.82** |

**Reading: the ~9% draft-pass bandwidth saving was consumed exactly by acceptance loss.** The two
cancelled. A pre-registered null branch was taken.

**Verdict: no adoption; the stock pinned recipe stays.** Not worth re-testing DFlash2 drafter
quantization on this stack.

---

## 5. Docker cpuset pinning to the fast core cluster: inconclusive, inside the noise band

**What was claimed:** pinning the serving container to the ten performance cores (confirmed by
`lscpu` as the 3900 MHz cores, versus 2808 MHz for the efficiency cores) is worth +2-7%.

**What we measured:** pinned, n=6, pooled **37.9**; same-day unpinned control, n=3, pooled **35.5**.
Nominally +7%, but the two conditions ran **sequentially, not interleaved**, and the claim sits
entirely inside our ±10% day-and-composition band. A sub-10% effect judged against a
non-interleaved control is not a result.

**Verdict: null / inconclusive. Not adopted.** Worth re-testing only with an interleaved A/B/A design.

⚠ **Ops trap found here, worth more than the arm:** `docker update --cpuset-cpus ""` **silently
no-ops**: exit 0, value unchanged. It does *not* clear a pin. The only trustworthy undo is the
explicit full range, `--cpuset-cpus "0-19"`. Verify with `docker inspect` inside the same command
block: a silent no-op invalidates every control run after it. The container was left at the full
range.

---

## 6. ROCmI4 with an embedded MTP head: works, but slower on every workload

**What it was:** the **ROCmFPX** fork of llama.cpp serving **ROCmI4**, a third-party signed int4
build of Qwen3.8-27B for Strix Halo (14.5 GB against the usual 4-bit K-quant's 17.9 GB) with the
NextN multi-token-prediction (MTP) head shipped *inside* the GGUF, so no separate drafter file is
needed.

**What was claimed:** 40-50 t/s on the Strix Halo class, backed by the publisher's own coding
benchmark.

**What we measured**, all same day, same node, same harness (512 tokens, temperature 0, thinking off,
n=8). Our own DFlash2 stack appears twice, because its draft depth is a separate finding and the two
depths win different workloads:

| Arm | Code + prose | Prose | Pure code |
|---|---|---|---|
| ROCmI4 + MTP, their model card's settings (`n16 / p0.60`) | 27.84 | 23.66 | 34.08 |
| ROCmI4 + MTP, our tuning (`n8 / p0.8`) | not measured | not measured | 40.32 |
| Our stack (DFlash2), as shipped at draft depth 4 | ~31 | 26.67 | ~41 |
| **Our stack (DFlash2), tuned to draft depth 8** | **32.78** | ~24 | **50.81** |

**Our stack is ahead on every workload, at the right draft depth for each, and no cell in the table
reverses that.** Each percentage below is against the model card's settings in row 1, and is
derivable from the numbers in the table:

- **Code + prose: +17.7%** (27.84 to 32.78, at draft depth 8).
- **Prose: +12.7%** (23.66 to 26.67, at draft depth 4). Draft depth 8 costs about 10% on prose, so
  that arm drops to ~24 and is effectively level with theirs. Prose is the workload where the two
  stacks are closest, and it is the reason draft depth is chosen per server rather than globally.
- **Pure code: +49.1%** (34.08 to 50.81, at draft depth 8).

Supporting detail worth keeping:

- **We reproduced their bare baseline to 0.6%** (13.88 vs their 13.8), so the apparatus is sound and
  the later differences are real.
- Every number we measured from the ROCmI4 build falls **inside their own published 29-43
  live-service band**. We reproduce their real-world range; neither of us reproduces their 10-task
  pilot figure.
- **The model card's own draft settings were costing them about 18%** (34.08 to 40.32 on pure code,
  changing only `n-max` and `p-min`). Most of the gap was their configuration, not our toolchain.
- ROCmI4's **W4A4** activation-quantization build flag is **not** a general speedup: +7.0% on pure
  code, **−8.9% on mixed content**, because activation quantization perturbs the model's own
  distribution and costs draft acceptance.
- ⚠ **One honest loose end:** in a single free-running agent loop (n=1) ROCmI4 measured 16% faster
  on decode with the session's highest acceptance. The mechanism is coherent (the autoregressive MTP
  head drafts thinking traces well, off a smaller file). It was never repeated. It is recorded, not
  buried, but n=1 on an instrument with documented trajectory variance is not a reason to revisit
  the verdict.

**Verdict: do not adopt ROCmI4.** The genuinely valuable output of that evaluation was accidental:
it is what led us to the single DFlash2 draft-depth flag worth +23% on our own stack.

---

## 7. An unmerged llama.cpp fork with an adaptive draft-length controller: worse, and unstable

**What it was:** an unmerged llama.cpp fork (`github.com/LaurentZuijdwijk/llama.cpp`, prebuilt Vulkan
release `b10641`) adding `--spec-draft-adaptive`, a controller that varies draft length per request
instead of using a fixed draft depth, paired with the fork's own FP4 target and drafter quants.

⚠ **The repository is the coordinate.** The release tag and the asset filename both follow upstream
llama.cpp naming, so on their own they identify nothing — a `b10641` build exists upstream too. Record
which repository a build came from, or a later reader cannot tell these arms apart.

**What was claimed:** ~65 t/s for code generation.

**What we measured**, all arms same day, same node, same harness, n=8:

| Arm | t/s | Acceptance | Spread | Worst run |
|---|---|---|---|---|
| **Ours: our build + our Q4 target and Q8 drafter + fixed draft depth 8** | **50.74** | **88.9%** | **0.57** | **50.37** |
| Fork + their FP4 target and drafter, fixed depth 8 | 60.32 | 85.3% | 32.93 | 36.96 |
| Fork + their quants, **`--spec-draft-adaptive 3-7`** | 47.21 | 66.8% | 44.79 | 21.65 |
| Fork + **our** models, fixed depth 8 | 38.93 | 55.9% | 40.26 | 14.41 |

**Verdict: do not adopt.** Our configuration is 58-79× tighter in spread, and the best fork arm's
worst run falls **27%** below ours (36.96 against our 50.37, the gap taken against our own floor;
taken against the fork's 36.96 as the base instead, the same pair reads as ours sitting 36% above
theirs). The figure we quote is the 27%. For an unattended agent loop running overnight the floor
matters more than the mean. Adopting would mean an unmerged fork *plus* a third-party FP4 quant of
unverified provenance, for +18.9% on the mean (60.32 against 50.74) and 58× the variance.

Two findings from it that outlive the verdict:

- **`--spec-draft-adaptive` is worse than a fixed draft depth on the fork's own stack**
  (fixed depth 8 = +27.8% over adaptive, 60.32 against 47.21). Caught in its own log: the controller
  locks to a mean draft length of 2.00 for a whole request and never recovers, while its good runs
  sit at mean length 7.30, exactly where a fixed depth of 8 puts it by construction. **Adaptive buys
  no upside over a fixed block draft; it only adds a failure mode.**
- **What is real from the fork: prefill +12.6%.** Decode +0.17% = nil. Its work is prefill and
  speculation width, not decode.

**The most valuable cell of the whole evaluation was the contemporaneous control.** Our own build,
re-measured the same day, landed within 0.43% of the previous figure with identical acceptance, which
refuted the "it's thermal, it's session drift" explanation for the fork's instability. Without that
cell, every fork number would have been uninterpretable.

---

## 8. SGLang as a second serving engine on the Strix Halo class: 10% against a 15% bar

⚠ **This verdict does NOT carry to the DGX Spark class.** On GB10, SGLang is our fastest option and
the basis of the flagship stack. Engine verdicts are hardware-class-local.

**What we measured on Strix Halo**, pre-registering a 15% adoption bar before running:

- **Decision cell** (4 concurrent × 15 sequential calls, i.e. the chain wall-clock a user actually
  perceives): **216.3 s versus 240.0 s for the incumbent = −9.9%.** Margin was 5.9× the within-arm
  spread, so the number is solid; it simply did not clear the bar.
- **This retired an earlier "+29%" figure**, retired 2026-08-11. That came from a closed-loop
  harness at 256-token replies on a memory budget 5.8× larger than production's. Same-day,
  matched-config measurement returned about a third of it.
- **Single-stream it loses** (21.43 vs 24.5 t/s, −12%) and wins the concurrent chain ⇒ its edge is
  batching and scheduling, not decode.
- A feared structured-output memory leak **did not reproduce** (two blocks of 500 requests; the
  second block showed *exactly zero* growth), and it survived a mid-decode client disconnect. So the
  rejection is on margin, not on a defect.

**Against:** an unmerged, single-maintainer patch set with no official SGLang support for this chip.

**Verdict: it builds, it runs, and it was measured. It is not a reason to switch.** Flip
conditions: upstream support or a second maintainer appears, we add different AMD silicon,
or the 9.9% margin is later judged worth it with the real number in hand.

---

## 9. No engine flag closed the vLLM → llama.cpp gap on gpt-oss-20b

**The measurement**, apparatus-matched (same client, same port, same GGUF-equivalent weights, same
node, same day, 1 user, 512 tokens on the Strix Halo class):

**vLLM 12.96 t/s versus llama.cpp 75.80 t/s = 5.85×.** Both engines' two rounds agree to <0.1%.

**We went looking for the config fix and it does not exist.** Relaunching without eager mode
(i.e. letting HIP graphs capture; verified capturing 9×, so the known graph-capture bug does not
bite this build) gave **12.96 versus 13.00**. Two configurations one flag apart, matching to ~0.1%.
Graph capture and compilation are a **null** on this model. A dispatch-bound explanation may still be
right, but no flag we can reach touches it.

**Two honest qualifications:**

- **~1.4× of the gap is not the engine.** "Same model" is false: the official checkpoint quantizes
  only the MoE experts and leaves attention, router, embeddings and output head in BF16; the GGUF
  re-quantizes those. Engine-attributable is ~4.2×; deployment-attributable is 5.85×. Both are true,
  they answer different questions.
- **Roofline check:** vLLM reads ~3.8 GB/token ⇒ a ~57 t/s ceiling, and measured 12.96 = **23%** of
  it. llama.cpp reads ~2.6 GB/token ⇒ ~83 t/s ceiling, measured 75.80 = **~91%**. llama.cpp is at the
  memory-bandwidth wall; no engine or fork improves on that.

**Verdict:** the cheap escape hatch is shut. But migration was **not** decided on this benchmark.
It hinges on whether the consuming application sends a request shape that llama.cpp silently
mishandles (we demonstrated two silent failure modes on our own binary: combining a response-format
constraint with tool definitions discards tool calling entirely, HTTP 200 and no warning; and
numeric minimum/maximum on a float field are silently ignored, with a schema capped at 5 returning
3.8e26). **A throughput number does not decide a migration when a correctness veto is live.**

---

## A note on all of the above

**These are point-in-time results against specific builds, specific model revisions and specific
engine versions.** Every entry names the arm that lost, not a permanent property of the technique.
Engine releases, drafter retrains and quantization toolchains move quickly, and several of these
verdicts would change if the corresponding upstream work landed, which is exactly why the receipt is
recorded next to the verdict rather than just the conclusion.

What does *not* expire is the method: same day, same instrument, one variable, a contemporaneous
control for the incumbent, and a pre-registered bar. Most of the entries above were closed by a
control cell rather than by the arm itself.
