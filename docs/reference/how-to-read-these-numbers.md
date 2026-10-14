# How to read these numbers

For a technical reader who is about to compare our figures against ones posted publicly for the same
hardware and the same model, and wants to know why they disagree.

Short answer: **almost all of them are correct, and they are measuring different things.** For the
same model on the same hardware class, publicly posted single-stream and aggregate figures span
roughly **50 to 360 tokens/sec**. That is not a controversy to resolve. It is four independent
variables moving at once.

**Start with the checklist below.** The sections after it are the evidence for each question, and you
only need the ones a figure actually fails.

---

## The checklist: judging someone else's numbers

**Eight questions, in the order that kills a bad figure fastest.** Each links to the section of this
page that explains it, with the evidence. Stop as soon as one lands.

★ **"Not stated" is an answer, and it is usually the answer.** Most published figures will not tell
you the thinking mode, the acceptance rate, or whether concurrent sessions shared a prefix cache.
That does not make the figure dishonest — it makes it **incomparable to ours**, which is a more
useful verdict than "wrong". If you cannot establish what a number includes, say so and stop, rather
than reasoning about the gap.

1. **Was thinking on or off?** The largest single factor, so ask it first. A thinking-off cell and a
   thinking-on cell are not comparable at all.
   → [§1, *Thinking mode on versus off*](#thinking-mode-on-versus-off)
2. **What workload, and how draftable is it?** Under speculative decoding, predictability *is* the
   variable — pure code, agentic work and prose differ by 2× on one stack in one day.
   → [§1, *Workload shape*](#workload-shape)
3. **Single stream or aggregate — and *which* aggregate?** Both can describe the same box in the same
   hour, and our two aggregate bases differ by 30-40% on one run.
   → [§1, *Single-stream versus aggregate*](#single-stream-versus-aggregate)
4. **Is an acceptance figure logged beside the rate?** Without it, a speculative-decode throughput
   number is partly a measurement of what the model happened to write.
   → [§2, *The hidden variable*](#2-the-hidden-variable-speculative-decode-acceptance)
5. **Is it a bench number or a served number?** No HTTP layer, no chat template, no sampler, no
   scheduler, batch size 1 — never compare a bench figure from one apparatus to a served figure from
   another. → [§4, *A raw generation benchmark is not a serving number*](#4-a-raw-generation-benchmark-is-not-a-serving-number)
6. **In a concurrency ladder, did the sessions share a prefix cache?** If they did, every rung is
   fiction. → [§5, *Concurrency figures carry their instrument*](#concurrency-figures-carry-their-instrument)
7. **Is the claim larger than the instrument's own band?** Ours is **±10%**, and no sub-10% claim is
   ever judged against another day's baseline.
   → [§3, *Our ±10% day-and-composition band*](#3-our-10-day-and-composition-band)
8. **Is it a controlled A/B, or a capability statement?** A controlled A/B means same day, same
   instrument, one named variable, and a contemporaneous control.
   → [§5, *Cross-model rows are capability statements*](#cross-model-rows-are-capability-statements)

---

## 1. Why published figures for the same box range 50 → 360 t/s

### Thinking mode on versus off

The largest single factor. A reasoning model at its default effort spends the great majority of its
generated tokens on reasoning: on one published Flash-Next run **86% of generation was reasoning
tokens, and disabling thinking cut a 55-second task to 15 seconds.** Our own agentic replay measured
**−72% tokens** going from the highest reasoning effort to `low` — on the 27B. ⚠ **The lever is
stack-specific and does not transfer**: on the 180B long-context model, low effort produced *more*
tokens. See [numbers.md §4](numbers.md#4-reasoning-effort-an-agent-loop-lever-not-a-per-request-one).

Consequently, a published "64-78 t/s single-stream" cell for our exact stack is a **thinking-off,
decode-only, temperature-0, draftable-content, median-of-4** measurement. It does not contradict our
35-41 t/s thinking-on agentic band. The same source's mixed-chat cell measures ~30, *below* ours.

### Workload shape

Under speculative decoding, **predictability is the variable**. Ranked from best to worst on our own
measurements of the same stack, same day:

| Workload | Rate on the flagship DGX Spark stack |
|---|---|
| 512-token pure code | ~42-50 t/s |
| Agentic replay (thinking on, tools, multi-turn) | ~35-41 t/s |
| 512-token prose | ~25 t/s |

File-edit and rewrite work sits higher still on other stacks (~82 t/s measured for a 180B model whose
copy-drafter engages on edits). Published benchmark suites usually name their workload as "fresh
generation" (deliberately low predictability) or "edit-heavy" (answer largely pre-specified; one
such author calls their own edit cell "synthetic and likely optimistic"). Comparing a fresh-generation
cell to an edit-heavy cell tells you nothing about the hardware.

### Single-stream versus aggregate

A "360 t/s" figure is aggregate across 16 concurrent streams. ⚠ **The publisher of that figure also reports ~26.8 t/s per stream — and 360 ÷ 16 is 22.5, not 26.8.** Both can be theirs and both can be right: an aggregate measured over the full window and a per-stream rate measured while all 16 are running are different bases. When a published pair does not divide out, that is usually what it means — ask which base each number used before calling either one wrong. A "50 t/s" figure
is one stream. Both can describe the same box in the same hour. **Always read the concurrency**
(`c=N` on these pages means N sessions in flight at once).
And when you do read an aggregate, check *which* aggregate. See
[numbers.md §2](numbers.md#why-two-aggregate-numbers) for the steady-state versus full-window
distinction; they differed by roughly 30-40% in our own 8-session measurement (~162 steady-state
against ~115-125 full-window).

### Short synthetic prompts versus real multi-turn sessions

A battery of 8 short prompts with 256-token caps is prefill-light and cache-friendly. A real agentic
session carries a long system prompt, tool results, and turn-to-turn context growth. Our own two
instruments, the short-prompt battery and the 5-turn agentic replay, measured **34.2 vs 40.8 t/s
at one stream on the same server**. Same box, different content, not drift. Both sit inside the
day-and-composition band of §3, which is why single-stream figures across these pages span roughly
33-41 t/s.

---

## 2. The hidden variable: speculative-decode acceptance

Every fast figure on these boxes comes from speculative decoding, and speculative decoding's
throughput is a direct function of **how many drafted tokens the target model accepts per
verification pass**. That number is a property of the *content being generated*, not of the hardware.

Measured on our own server, from the engine's own per-batch statistics:

- **~4.03-4.05 accepted tokens per pass (of a budget of 8) on real agentic content**, across 299 and
  339 batches in two independent sessions — about **50%** of the budget.
- **~64.6% acceptance on pure code** on the same hardware class — ⚠ **a different stack**: that figure is the llama.cpp GGUF one (208/322), where the bullet above is the SGLang flagship. i.e. roughly **5.2 tokens per pass**.

⚠ Those two reached us in different units, tokens-per-pass and percent-of-budget. They are converted
here so they can be compared; never set "4.03" against "64.6%" as though they were one scale.

On our measured slope, **throughput moves roughly 10 t/s per accepted draft token.** That single
relationship explains most of the spread between published cells: thinking-heavy content drafts
poorly and pins around 38-41 t/s, while copy-ish and code-ish content drafts well and hits 50-102 t/s
*within the same run*. The pooled rate you report is therefore partly a measurement of **what the
model happened to write**.

Published domain spreads match: one speculation benchmark reports 2.46 accepted tokens on code versus
1.87 on roleplay (~30% apart); the drafter lineage's own numbers show 4.2 on maths versus 3.0 on chat.

**Practical rule we adopted: never interpret a pooled tokens/sec figure without its acceptance
covariate logged beside it.**

---

## 3. Our ±10% day-and-composition band

This is our own instrument's honest width, and it is the reason every figure on these pages is a
range.

**What we observed:** identical stock configuration, identical 5-turn transcript, identical server,
and pooled **40.77 t/s one day, 35.5 / 37.7 / 37.4 across three batches the next**. About a 10%
swing with nothing changed.

**What we ruled out:** we restarted the service and re-measured on a fresh server (37.4, the same
band), which eliminated uptime and cache-state drift. We separately confirmed the engine runs with kernel
tactic autotuning **disabled**, which eliminates boot-to-boot kernel-tactic redraw as the mechanism
(a real effect elsewhere: a third party measures 4.3-8.1% boot-to-boot variance from exactly that,
on an autotuning configuration).

**What explains it:** generation-content variance is sufficient on its own. The replay free-generates,
so turn 1 alone ranged from 54 to 6,455 tokens across runs. Content mix shifts run to run, acceptance
follows the content, and (per §2) rate follows acceptance. Our measured same-day acceptance range
(3.80-5.26) is several times what would be needed to produce an 8% rate shift.

**The rules that came out of it:**

1. **No sub-10% claim is ever judged against a baseline from another day.** Same-day, ideally
   interleaved, controls are mandatory.
2. **Quote ranges, always.** "~35-41 t/s agentic depending on generation mix", never "40.8".
3. Effects an order of magnitude larger than the band (the reasoning-effort lever, for instance) are
   unaffected by this and can be called from back-to-back arms.

**Industry practice agrees on the magnitude:** MLPerf treats 5-10% as same-system noise, and vLLM's
own performance CI proposes ±10% bands. What is unusual is publishing the band at all: spec-decode
throughput is generally reported without error bars.

---

## 4. A raw generation benchmark is not a serving number

`llama-bench` and its equivalents run the model directly: **no HTTP layer, no chat template, no
sampler, no scheduler, batch size 1.** We measured the gap on our own hardware: **llama-bench
overstated served throughput by 6.6%** (a bench figure of 81.16 t/s against 75.80 t/s served,
same model, same box, same build, same day). ⚠ **That pair does not hold generation length constant**
— the bench cell is `tg128`, the served figure a 512-token completion — so it bounds the size of the
apparatus gap rather than isolating it. **Say which base you are quoting**: 6.6% is the shortfall
against the bench figure; taken on the served figure as the base, the same pair reads as the bench
sitting 7.1% above it.

It is also structurally blind to things that matter:

- **Correctness.** A benchmark reports tokens per second whether or not the tokens are right. We have
  a documented case of a stack producing wrong output at full speed.
- **Speculation.** Batch-1 unspeculated generation tells you nothing about a contribution that
  targets batches of 3-8. The unmerged llama.cpp fork in
  [tested-and-rejected.md](tested-and-rejected.md) measured **+0.17% on `llama-bench tg128`**
  (81.16 → 81.30, **2026-08-26**) while its real contribution was prefill: **+12.6% on `pp512`,
  which is itself a llama-bench figure**, and **about +20% served** (578 ms → 480 ms on an 83-token
  prompt over HTTP). So the bench was not blind to the prefill gain — it was the *decode* cell that
  predicted nothing. ⚠ A second measurement of the same two builds on **2026-08-31** reads 81.11
  stock against 81.42 fork (≈ +0.38%), and that is the pair [numbers.md §1](numbers.md#1-the-table)
  quotes. Two decode nulls five days apart, not a disagreement.
- **Serving overhead.** Prefill on a short prompt is dominated by a fixed per-request cost
  (~560-585 ms for an 83-token prompt on one of our stacks), which a bench harness never pays.

Corollary: **never compare a bench figure from one apparatus to an HTTP figure from another.** The
ratio you get is a property of the two harnesses, not of the thing you were trying to measure.

---

## 5. How to read our own tables

### "Agentic replay" means this specifically

One real coding-agent session was captured against a live server — 5 turns, with the system prompt
and tool results a real agent produces — and frozen as a fixed transcript. Every arm replays **the same transcript** against
whichever stack is under test, with a warm-up run and n=3.

We use it because the alternative, letting an agent free-run, is a poor A/B instrument. Two arms of
the *same* model and quant, differing only in speculation flags, produced **1,330 versus 2,398
generated tokens** because one near-tie token flip sent the agent down a different path. Trajectory
variance dominates the effect you are trying to measure.

What replay does *and does not* give you:

- ✅ Same prompts, same turn structure, same context depth across arms.
- ✅ Realistic within-session cache reuse.
- ❌ It still free-generates the *responses*, which is where the ±10% band of §3 comes from.
- ❌ **Zero tool-execution idle time.** A real agent spends wall-clock waiting on tools; replay does
  not. Any "tasks per hour" figure derived from replay is arithmetic on the token side only.

### Concurrency figures carry their instrument

The 8-session ladder used unique per-session preambles specifically so the sessions would **not**
share the server's prefix cache. Without that, concurrent sessions of an identical transcript
collapse onto one cached prefix and every rung of the ladder is fiction. If you are reading someone
else's concurrency ladder, this is the first thing to check.

### ★ "Decode" and "end to end" are different numbers. Never compare across them

This is the mistake that has cost us the most time, and it cost it *internally*, on our own tables.

- A **decode** figure counts generated tokens against generation time. It excludes prefill and
  per-request overhead. The depth tables in [numbers.md §6](numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs)
  are decode figures.
- A **full-window aggregate** or **end-to-end** figure divides the tokens returned by the whole wall
  clock of the request, overheads included. The concurrency ladders are these.

On the long-context stack the same healthy server reads as **33-36 t/s decode** (needle-retrieval
probe, largely draftable output) and **21.8 t/s full-window aggregate at one session** (5-turn
agentic replay). Both are correct. Part of the gap is the prefill and overhead one includes and the
other does not — and part of it is that they are **different workloads at different depths**, which
§2 says is the dominant variable in its own right. That is the point: two numbers can differ for
several reasons at once, and none of them is a regression.

We once measured a single plain end-to-end request at **18.6 t/s**, compared it against the
decode-only 33-36, and spent an hour hunting a "2× regression" through proxies, speculative decoding
and thermals. **There was no regression at all** — the decode-only figure was never the comparable
one. Against the 21.8 full-window figure it is −15%, which is **outside the ±10% band of §3** and so
would need a same-day control before anyone called it anything; on a single request it is not a
result either way. The lesson is not "−15% is fine", it is that **the 2× was manufactured by the
comparison**.

⇒ **Before comparing two throughput numbers, state what each one includes. If you cannot, you cannot
compare them** — and that applies to two of our own rows as much as to somebody else's blog post.

### Cross-model rows are capability statements

Our replay ladder puts Gemma-4 26B at ~81 t/s and Qwen3.8-27B at ~35-41 t/s on the same box. That is
**not** a controlled A/B: different model, different quantization, different drafter, different
quality profile. It answers "what can this box do with this model", not "which is better". The
slower model is the stronger one on published reasoning and coding benchmarks.

A controlled A/B on our pages means: same day, same instrument, same transcript, one variable named
explicitly, with a contemporaneous control run for the incumbent arm.
