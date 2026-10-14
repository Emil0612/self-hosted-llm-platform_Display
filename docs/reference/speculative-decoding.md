# Speculative decoding: the mechanism

Most fast single-stream figures in this repo come from speculative decoding. This page explains
**how it works and why it is free**. It deliberately stops there:

- **How much it is worth on a given workload**, and why that varies so much, is
  [how-to-read-these-numbers.md §2](how-to-read-these-numbers.md#2-the-hidden-variable-speculative-decode-acceptance).
- **When to tune it versus tuning concurrency** is
  [which-model-for-which-job.md](which-model-for-which-job.md#two-decisions-this-table-does-not-make-for-you).
- **The measured numbers** are in [numbers.md](numbers.md).

---

## The problem

Generating one token requires reading the model's entire active parameter set out of memory, and the
arithmetic done on those bytes is trivial by comparison ([why-this-hardware.md](why-this-hardware.md)).
So the processor spends a single-stream decode mostly waiting on memory, with most of its arithmetic
capacity idle.

## The insight

**Once you have paid to read the weights, checking many candidate tokens costs almost the same as
checking one.** The expensive part — moving the weights — happens either way. Verifying eight
candidates instead of one adds arithmetic, and arithmetic is the resource that was sitting idle.

## The mechanism

1. A cheap **drafter** proposes the next N tokens. It is small and fast: a small model, a lightweight
   prediction head bolted onto the main model, or in the simplest case a lookup that copies text
   already present in the context.
2. The **target** model — the real one — runs **one** forward pass that evaluates all N candidates at
   once, and produces what it would have generated at each position.
3. The drafted tokens are kept up to the **first** position where the target disagrees. That token is
   replaced with the target's own choice, the rest are discarded, and the cycle repeats.

Accept six of eight and you produced seven tokens for roughly the cost of one. Accept none and you
produced one, having paid a little wasted arithmetic — which is why the technique has a floor near
unspeculated speed rather than a risk of being much worse.

★ **It is lossless.** The output is what the target model would have produced on its own. Speculation
changes how fast tokens arrive, never which tokens they are. A drafter cannot make the model dumber,
and a better drafter cannot make it smarter — only faster.

⚠ **That symmetry is worth stating to anyone who worries about quality:** a bad drafter costs speed,
not correctness. Nothing here trades accuracy for tokens per second.

---

## What we run

| Kind | What the drafter is | Where |
|---|---|---|
| **DFlash2** | a separate trained draft model | our Qwen3.8-27B stacks, both hardware classes |
| **MTP** (multi-token prediction) | extra prediction heads on the model itself | Gemma-4 26B, and the long-context 180B stack |
| **n-gram / copy** | no model — copies matching text from the context | rewrite and file-edit work |

The last one explains a result that otherwise looks strange: the 180B long-context model runs far
faster on **rewrites** than on fresh prose, because when much of the answer already exists in the
context a copy-drafter proposes long runs correctly. Fresh generation gives it nothing to copy.

## Why it is the only single-box speed lever left

Unspeculated decode already runs at a high fraction of the memory-bandwidth ceiling, so there is
little headroom to reclaim by tuning the plain path. Speculation is the one mechanism that breaks the
one-weight-read-per-token relationship at all — which is why our served single-stream rates sit
roughly **2-3× above the unspeculated floor**, and why the tuning attempts that did *not* pay were
mostly attempts to make an already-saturated path faster
([tested-and-rejected.md](tested-and-rejected.md)).

⚠ **Not all of that 2-3× is speculation.** The pairs it comes from also change engine and
quantisation, and the floor is a bench figure while the served rate is not. Read it as what a whole
served stack buys over an unspeculated baseline; the breakdown is in
[which-model-for-which-job.md](which-model-for-which-job.md).

★ **Its value is workload-dependent to a degree that surprises people** — predictable text drafts
well, exploratory reasoning drafts poorly, and the same configuration is worth very different amounts
on different jobs. That is the single biggest reason published figures for identical hardware
disagree, and it is covered in
[how-to-read-these-numbers.md §2](how-to-read-these-numbers.md#2-the-hidden-variable-speculative-decode-acceptance).

⚠ One operational note that belongs with the mechanism rather than with any single recipe: draft
budgets are **not** monotonic, and the effect has a sign that depends on the workload. **Past the
drafter's natural block size our own sweep came back flat** — raising the budget beyond it bought
nothing and lost nothing. **Up to that size it matters in both directions:** on one of our stacks
`--spec-draft-n-max 8` beat `n-max 4` by **+23% on pure code**, while a raised-budget arm **lost ~10%
on prose** on the same rig. (The prose arm raised the budget past the block size rather than to it;
the two settings were separately shown to draft byte-identically, which is why the comparison holds.) So a third-party report of a larger budget measuring *worse* on chat-shaped work is
consistent with what we measured ourselves — a budget tuned on code can cost you on prose. Any change here needs a same-day measurement on the workload you actually run, not a setting
copied from a benchmark.
