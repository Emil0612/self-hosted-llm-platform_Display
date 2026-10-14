# Why this hardware

Every other page here tells you what these machines do. This one says why we own them, and — just as
usefully — what they are not for.

**The short answer.** Generating tokens is limited by memory bandwidth, not by arithmetic. That one
fact decides everything else: it is why these boxes are competitive at all, why they will never win
a raw-speed comparison against datacentre hardware, and why the thing they are actually good at is
holding large models cheaply and serving them privately at modest concurrency.

**The case, stated in terms we have measured.** These machines run models we could not otherwise run
at all — a 180B-parameter model serves from a single node here. They serve real multi-turn agent work
at concurrencies we have measured rather than estimated, documented throughout
[numbers.md](numbers.md). They are always available and not metered per token. And no prompt,
document or tool result leaves hardware we control. Every one of those is a checkable claim.
**The one thing we have not established is cost** — see §4, which says what would settle it.

---

## 1. The governing physics: decode is memory-bandwidth-bound

To generate **one** token, the machine must read the model's entire active parameter set out of
memory. Not some of it — all of it, once per token. The arithmetic performed on those bytes is
trivial by comparison, so for single-stream generation the processor spends most of its time waiting
on memory rather than computing.

That gives a ceiling you can calculate before running anything:

> **tokens/sec ≈ memory bandwidth ÷ bytes read per token**

**Measured on a Strix Halo (gfx1151) node, and it is per quantisation format, not one number for the
machine:** **218 GB/s** on K-quants (Q4) and **228 GB/s** on Q8. The format matters because a
heavily-structured quantisation costs dequantisation work per byte, leaving less of the bandwidth
usable. ★ **When predicting a rate, pick the constant for the format you are actually serving** — a
Q8 model predicted with the Q4 constant comes out ~5% low. **No constant has been measured for BF16
on this hardware**; the two above only bracket it.

The formula reproduces reality closely enough to plan with. A dense 31B model at BF16 reads roughly
62 GB per token, which predicts about 3.5 t/s — and that is what it measured
([numbers.md §1](numbers.md#1-the-table)). ⚠ Note what that prediction had to assume: **there is no
measured BF16 constant**, so it uses the bottom of the 218-228 bracket above. Anywhere in that
bracket lands between about 3.5 and 3.7 t/s, which is why the prediction survives the uncertainty —
but the constant is borrowed, not measured, and a case where the answer turned on 5% would not
survive it. The ceiling is not a theory; the input to it is partly an assumption.

★ **The practical corollary: bytes-per-token is the lever, not the processor.** A smaller
quantisation, or a model with fewer *active* parameters, moves this number directly. A faster chip
does not.

---

## 2. Active parameters, not total, decide decode speed

This is the single most useful thing to know when someone proposes a model.

Only the parameters a token actually activates get read. A mixture-of-experts model with 180B total
but ~6B active reads far less per token than a dense 31B, and it is the dense model that is slower.
Measured on the same hardware class:

| Model | Shape | Single-stream decode |
|---|---|---|
| Gemma-4 26B-A4B | MoE, ~4B active | **24.5-24.6 t/s** on a plain completion |
| Gemma-4 31B | dense | **~3.5 t/s** |

Nearly the same parameter count on the label; a **7× difference** in what the hardware can do with
it. ⚠ These are two different served configurations rather than a controlled comparison of one
model, so read it as the shape of the effect, not a precise multiple. Full context in
[numbers.md §1](numbers.md#1-the-table).

⇒ **"How big is it" is close to the wrong question. "How much of it moves per token" is the right
one.** This is also why a 180B model runs at all on a single box here — see
[which-model-for-which-job.md](which-model-for-which-job.md).

---

## 3. Where we do not win, stated plainly

**Raw speed. A datacentre GPU is faster in absolute tokens per second, at any batch size.** This is
not a "competitive until the batch gets large" story — it is faster at the small end too, and the
gap widens as batching lets it recover the arithmetic capacity that sits idle during decode.

⚠ **We have no measurement of our own for that gap**, and no benchmark or cited source behind the
rough figure we use for it — **roughly an order of magnitude per box**. **Treat that as a framing,
not a number to quote**, and do not put a precise multiple in front of anyone.

Also not a win:

- **High-batch throughput.** As concurrency rises, decode shifts toward being compute-bound, which is
  exactly where the hardware we did not buy is strong.
- **Anything latency-critical at scale.** Our aggregate figures come from modest concurrency; see
  [numbers.md §2](numbers.md#2-concurrency-the-measured-ladder).

---

## 4. Where we do win

**Capacity for the money.** Unified memory means a large model *fits*: a ~180B-parameter model runs
on a single node here. The comparison is not "we are faster", it is "this runs at all, on hardware at
this price". ⚠ We have also served a 235B model across two nodes of the other hardware class, but
that is not documented here, so treat it as background rather than as a reproducible claim.

⚠ **"Capacity for the money" is an architectural argument, not a costed one.** We have never compared
the total cost of owning these machines against renting or buying datacentre GPU time, and nothing in
this repo does.

★ **That is a bounded piece of work, not an open question.** The hardware cost is known, utilisation
is measurable from the gateway's own request logs, and rented-GPU pricing is public — so it is
roughly a half-day exercise. **If a decision turns on actual money, do that comparison rather than
reading a cost claim into anything on this page.**

**Latency-tolerant volume.** Work that is patient — overnight loops, batch processing, background
agents, long-document work — converts capacity into useful output without needing speed parity.
A machine that is slower per token but always available and not metered has a real place.

**The data does not leave.** Prompts, documents, tool output and the model weights stay on hardware
we control and on a private network; by design, nothing is sent to a third-party API.

⚠ **Stated as a design property, not as a market claim.** Whether
anyone *chooses* this over a hosted API for that reason is **not something we have established**, and
it should not be presented as a proven commercial differentiator until it is.

---

## 5. The silicon is not the differentiator; the software is

Worth internalising before anyone proposes buying a different box to go faster.

**Unspeculated decode on the two hardware classes we run is a tie.** The same 27B model, same engine,
measures **about 12.1-12.2 t/s on both** a Strix Halo (gfx1151) node and a DGX Spark (GB10) node.
⚠ Quote that as a tie and nothing more precise: the two machines' model files were later found to be
different revisions of the same quantisation, so no percentage can be read off the pair
([numbers.md §1](numbers.md#1-the-table)).

Two very different processors landing on the same number is exactly what section 1 predicts, because
both are reading a similar number of bytes per token at a broadly similar bandwidth.

**What actually separates them is prefill.** ⚠ **The measured pair here is a different model** —
gpt-oss-20b, not the 27B above — on the same llama.cpp project: prompt processing ran about **2.3×
faster** on the GB10 class. ⚠ **Two things stop that being a clean
single-variable result**, and both matter: the two sides ran **different backends** — Vulkan on the
AMD node against CUDA on the GB10 one — and the AMD figure came from a build we rejected for other
reasons. This repo treats backend as decisive elsewhere ([numbers.md §6](numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs)
spends a paragraph on exactly that), so read 2.3× as the shape of a real and large gap, not as a
measured multiple. The direction is not in doubt; the number is.

★ **Prefill is the one that matters for agent work,** where the machine reads a large context on
every turn and writes comparatively little. It is also why the same box can look mediocre on a
generation benchmark and good on a real agent loop.

⇒ **Rates on these machines are set far more by engine, quantisation and speculative decoding than by
which processor is underneath.** Our own largest single-stream gains all came from software: see
[speculative-decoding.md](speculative-decoding.md), and
[tested-and-rejected.md](tested-and-rejected.md) for the ones that did not pay.

---

## 6. What this means when someone asks for a decision

- **Choosing hardware?** Ask what bytes move per token and how much memory the model needs. Do not
  ask which chip is faster.
- **Someone says a box is slow?** Check the model's active parameter count and quantisation first;
  that is usually the whole answer.
- **Someone proposes new hardware to go faster?** Unspeculated decode is a tie across two very
  different processors. The lever is software, and the levers already tried are written down.
- **Someone compares us to a hosted API on speed?** They are right, and it is not the comparison
  this hardware is for.

