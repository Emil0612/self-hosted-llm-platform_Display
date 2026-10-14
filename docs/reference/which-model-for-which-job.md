# Which model for which job

A **routing table**: you bring the job, it points at the served configuration we have actually
measured for that shape of work, and at the recipe that reproduces it.

Every figure below is quoted **with its workload named**, because these rows measure different
things — agentic replay, fresh generation, decode at a named depth, a plain completion — and a
number without its workload is not a result. The full measurement behind each row, including
everything this page compresses, is in [numbers.md](numbers.md); if you are about to compare any of
it against a figure posted publicly, read
[how-to-read-these-numbers.md](how-to-read-these-numbers.md) first.

One row per measured configuration. ⚠ **Not all of them were served to clients at the same time** — how
clients reach the served models is in [connecting-clients.md](../connecting-clients.md). This is not a
ranking: the fastest row is not the best model.

| Job | Model + stack | Node class | What you get | What it costs | Recipe |
|---|---|---|---|---|---|
| **A shared agent endpoint for many people** | Qwen3.8-27B NVFP4, SGLang + DFlash2 | DGX Spark (GB10) | **8 concurrent agentic sessions at ~25 t/s each, ~162 t/s aggregate** at ~6.5k context; **~35-41 t/s** single-stream agentic replay ([§1a](numbers.md#1a-qwen38-27b-nvfp4-on-sglang), [§2](numbers.md#2-concurrency-the-measured-ladder)) | The ladder was run at **~6.5k context**. Repo-sized agent work runs at 20-30k+, where the measured 196,761-token KV pool binds first. **Do not size a repo-worker fleet on the 8** — at 32k the pool divides to about 6, and this stack has never been measured at that depth — pool ÷ (2 × context), the rule validated in [§3](numbers.md#3-context-and-capacity). ⚠ Dividing the pool by context alone gives 6 at 32k; that is a **ceiling with no occupancy allowance**, and over-provisioning on it does not fail cleanly — this stack answers pool exhaustion with silent retraction and re-prefill, a latency spike with no error. 12 and 16 concurrent were never measured | [dgx-spark/qwen38-27b-sglang-nvfp4-dflash2.md](../runbooks/dgx-spark/qwen38-27b-sglang-nvfp4-dflash2.md) |
| **One agent working alone, or an unattended loop overnight** | Qwen3.8-27B GGUF Q4_K_XL, llama.cpp Vulkan + DFlash2 | Strix Halo (gfx1151) | **~26-28 t/s** agentic replay and real agent loop ([§1g](numbers.md#1g-qwen38-27b-gguf-on-vulkan)). On pure code our own build measured **50.74 t/s at 88.9% acceptance, spread 0.57, worst run 50.37** (n=8) — ⚠ **at `--spec-draft-n-max 8`, not the depth this recipe ships**, which measures ~41 t/s on the same prompt — for an unattended overnight loop the floor matters more than the mean ([tested-and-rejected §7](tested-and-rejected.md#7-an-unmerged-llamacpp-fork-with-an-adaptive-draft-length-controller-worse-and-unstable)) | Ships at **`--spec-draft-n-max 4`**; `8` only on a server dedicated to code generation, where it is worth **~+23%**. The ~50-51 t/s pure-code cell is a **ceiling, not a planning figure**. Only 4 concurrent has been measured (**60-77 t/s aggregate**), nothing beyond | [strix-halo/qwen38-27b-llamacpp-vulkan-dflash2.md](../runbooks/strix-halo/qwen38-27b-llamacpp-vulkan-dflash2.md) |
| **A no-Docker, no-sudo option for the same model** | Qwen3.8-27B GGUF Q4_K_XL, llama.cpp + DFlash2 at `--spec-draft-n-max 8` | DGX Spark (GB10) | **~29-37 t/s** agentic replay (pooled ~33); **~41.5 t/s** maximally-draftable pure code at 64.6% acceptance; TTFT ~4.2 s on a big-prefill turn ([§1b](numbers.md#1b-qwen38-27b-gguf-on-llamacpp)) | Concurrency **not measured** at all on this stack. The no-Docker, no-sudo property is stated by the recipe itself and is the reason to pick it — worth knowing that the flagship SGLang alternative *is* containerised | [dgx-spark/qwen38-27b-llamacpp-dflash2.md](../runbooks/dgx-spark/qwen38-27b-llamacpp-dflash2.md) |
| **Long documents, a handful of users** | Qwen3.8-Flash-Next 180B NVFP4, vLLM, PLE table mmap'd from local NVMe, MTP=2 | DGX Spark (GB10) | **~33-36 t/s** decode and **flat from 8k to 128k context** (~28 t/s at 248k); prefill ~2,100 t/s, linear in depth; retrieval **verified correct at 247,899 tokens**, and **8/8 correct under load at ~33k** on a separate single-turn probe, which admitted seven of eight clients where the multi-turn ladder admitted eight; ~60 s to first token at 128k. **Only the first question about a document pays that: a repeat question over the same context is roughly an order of magnitude cheaper** (one measured pair, ~14½ s cold vs ~1½ s warm on a ~22k-token document). **Measured KV pool 514,149 tokens**; the server **throttles admission rather than failing** — no preemption and no failed request at any depth ([§1e](numbers.md#1e-flash-next-180b-nvfp4-on-vllm), [§6](numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs)) | **How many users depends on their context depth. Eight fit at ~33k** (31-34 t/s aggregate, ~4 t/s each); **past ~35k the pool binds** and only 5-7 are admitted, with aggregate falling ~62% between ~33k and ~45k. At ~6.5k it stops gaining past four. ⚠ **One client prefilling 128k costs the others over half their rate, and the worst-hit sessions ~80%**, so *every* one of those figures holds only if nobody submits a large document. The stack also **withholds streamed output and releases it in a fixed ~19-second tail, which makes every stream-timestamp metric on it wrong in the optimistic direction**. ⚠ **Two prerequisites before this serves anyone but you.** It needs a gateway in front of it if more than one person is to hold a key. It installs **no systemd unit**, but it does not need one: the container carries a `--restart unless-stopped` policy that brought it back after a deliberate reboot, tested once on 2026-09-09, serving again ~13½ min later. ⚠ That policy handles a container that *exits*, not one that is alive and wedged, so a liveness probe is worth adding | [dgx-spark/qwen38-flash-next-180b-vllm-nvfp4.md](../runbooks/dgx-spark/qwen38-flash-next-180b-vllm-nvfp4.md) |
| **Long documents, one patient user, on the AMD node** | Qwen3.8-Flash-Next 180B GGUF 4.27bpw Q4_K_M, stock llama.cpp Vulkan | Strix Halo (gfx1151) | **~24-25 t/s** decode at 8k; **~24 t/s** on agentic replay; retrieval **verified correct at 8k / 32k / 64k / 128k** ([§1f](numbers.md#1f-flash-next-180b-gguf-on-vulkan), [§6](numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs)) | **Single slot**: a second request queues behind the first, and behind a 128k prefill that is a **~12-minute stall**. Decode falls to **~12 t/s at 128k** and prefill from ~400-420 to ~170 t/s. ⚠ On this node the **27B beats Flash-Next on agentic work on both axes** (~27 vs ~24 t/s decode, and 10.6 s vs 24.9 s TTFT on the large-prefill turn) | [strix-halo/qwen38-flash-next-180b-llamacpp-vulkan.md](../runbooks/strix-halo/qwen38-flash-next-180b-llamacpp-vulkan.md) |
| **Rewrites and refactors of existing files** | Qwen3.8-Flash-Next 180B UD-Q4_K_XL, llama.cpp native, `ngram-mod` spec | DGX Spark (GB10) | **~82 t/s** on file-edit / rewrite work, warm — this model's copy-drafter engages on edits ([§1d](numbers.md#1d-flash-next-180b-gguf-on-llamacpp)) | Fresh generation on the same stack is only **~22-26 t/s** (code 23.0, prose 23.0, agentic replay pooled 25.8), so this row is an edit-shaped figure and nothing else. Concurrency **not measured** | [dgx-spark/qwen38-flash-next-180b.md](../runbooks/dgx-spark/qwen38-flash-next-180b.md) |
| **Multi-user serving on the AMD node** | Gemma-4 26B-A4B BF16, vLLM 0.23.1 (barebones ROCm bundle) | Strix Halo (gfx1151) | **~168 t/s aggregate at 32 users** at `--max-num-seqs 64`, and **221 t/s at 64 users / `--max-num-seqs 256`** (which also needs `--max-num-batched-tokens 16384`). ⚠ **The recipe ships neither**: it leaves the flag out at the engine default 128, a point between those two arms with no throughput measurement of its own — the finding was that an inherited cap of 16 cost ~37%, not that 64 is the right value; 256-token replies scale 8 → ~90, 16 → ~145, 32 → ~200 t/s aggregate ([§1h](numbers.md#1h-gemma-4-26b-a4b-bf16-on-vllm)) | Single-stream is only **24.5-24.6 t/s** on a 400-token plain completion. That is roughly 85-90% of the BF16 roofline, but **no bandwidth constant has been measured for BF16 on this hardware** — the measured 218 (Q4) and 228 (Q8) constants only bracket it. ⚠ That single-stream figure is on **short prompts**: the same stack measures **~16 t/s at 26k context**, so quoting 24.5 for long-context work overstates it by ~50%. ⚠ That 26k cell was measured on a wider context window than this recipe ships (`--max-model-len 16384`), so it shows the shape of the depth penalty rather than a depth this configuration accepts. Leaving `--max-num-seqs` at 16 caps 32 users at 122 t/s | [strix-halo/gemma4-26b-vllm-serving.md](../runbooks/strix-halo/gemma4-26b-vllm-serving.md) |
| **The fastest agentic rate we have measured** | Gemma-4 26B-A4B Google QAT `q4_0`, llama.cpp + MTP drafter at `n-max 3` | DGX Spark (GB10) | **~81 t/s** agentic replay thinking off and **~87 t/s** thinking on; 86-101 t/s decode on a harder agentic task; MTP multiplier 1.63× at 68.1% acceptance ([§1c](numbers.md#1c-gemma-4-26b-a4b-with-mtp)) | ⚠ **This is a capability comparison, not a like-for-like A/B.** Gemma-4 26B at ~81 t/s and Qwen3.8-27B at ~35-41 t/s are different models with different quality profiles measured on the same replay; **the faster one is not the better one**, and the slower one is the stronger on published reasoning and coding benchmarks. Concurrency **not measured** — no `-np` ladder has been run on this stack | [dgx-spark/gemma4-26b-llamacpp-mtp.md](../runbooks/dgx-spark/gemma4-26b-llamacpp-mtp.md) |

## ⚠ This table routes on speed and capacity. It does not route on answer quality.

**Every column above is throughput, latency or capacity. None of it is evidence about which model
gives better answers**, and the repo should not be read as if the choice had been made on merit.

What we actually have:

- **No controlled quality comparison between any two configurations here.** No perplexity, no KL
  divergence, no shared eval battery. The 4-bit checkpoints have been screened only by task
  completion — an agent finished its coding task — which shows nothing observable broke, not that
  quality is preserved ([numbers.md §5](numbers.md#5-what-we-have-not-measured)).
- **One head-to-head, and it is not ours**: an upstream run of 72 trials scored Flash-Next **60/72
  against the 27B's 63/72**. That is the only direct comparison we are aware of, it favours the
  smaller model, and it is a single third-party result.
- **Reasoning-effort quality screens on the 27B passed** (72/72 on a hard-task battery at low effort,
  tying xhigh on the same runs). Those are screens against our own tasks, not certifications.

⇒ **Nothing here shows that one model is "smarter" than another.** Where quality matters to a decision,
treat it as unmeasured and say so.

★ **Recommended next piece of work.** A shared eval battery across the served configurations — one
prompt set, one scorer, same day — would close this. It is roughly half a day, bounded rather than
open-ended, and the largest remaining gap in this repo.

## Two decisions this table does not make for you

**Speculative decoding buys latency for one user; batching buys throughput for many, and they are
not the same lever.** Speculation is what lifts a single stream off its unspeculated floor — roughly
2-3× on these stacks, though ⚠ that ratio also spans an engine and quantisation change and compares a
bench figure against a served one, so read it as what a whole served stack buys rather than as
speculation's isolated contribution
([speculative-decoding.md](speculative-decoding.md)). What it is worth on any given day is a property
of **what the model happens to write**
([how-to-read-these-numbers.md §2](how-to-read-these-numbers.md#2-the-hidden-variable-speculative-decode-acceptance)).

Batching is the other axis, and it stops adding once the box is throughput-saturated — the ladder and
the two aggregate bases it can be read on are in
[numbers.md §2](numbers.md#why-two-aggregate-numbers). **Choose speculation settings for the person
waiting on one answer, and concurrency settings for the queue.**

**One large model per machine, so multiple model names means multiple machines.** These are
unified-memory boxes, and two large models do not coexist on one of them, so a genuine choice
between two names on one endpoint means two nodes — which makes "what does each node serve" a
decision to take before any key is handed out. The full version, including what a model swap costs
the people already using the old name, is in
[runbooks/gateway/ — *What decides the menu of models*](../runbooks/gateway/README.md#what-decides-the-menu-of-models).
