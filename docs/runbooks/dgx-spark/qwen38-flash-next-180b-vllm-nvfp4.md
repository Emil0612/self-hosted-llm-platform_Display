# Qwen3.8-Flash-Next 180B on a DGX Spark: vLLM + NVFP4, PLE table on local NVMe

```text
Verified against : DGX Spark class node (NVIDIA GB10, sm_121, arm64, 128 GB unified, 1 TB NVMe)
Engine / build   : vLLM, patched container image `qwen38-flash-dgx` built on the node by
                   blazux/qwen3.8-Flash-DGX at commit 656cdb0 (image sha256:d58dfc39…).
                   Locally built, so it carries no registry digest.
Model            : RadixArk/Qwen3.8-Flash-Next-NVFP4, revision 7b71922 (full:
                   7b719225242aacd3dbd3f9407468c2ee9a9d2594) — 206 safetensors, 135.25 GB (126 GiB)
Serve defaults   : MODE=nvfp4, PREFIX_CACHE=1, DET_TOPK=1, PORT=<engine-port>, CTX=262144, SEQS=8,
                   GPU_MEM=0.80, MTP=2
Date verified    : 2026-09-14 (stack first stood up and measured 2026-09-07; depth curve
                   2026-09-08; capacity, deep concurrency and correctness under load 2026-09-09;
                   install path, build and load timings, loopback posture and prefix-cache
                   behaviour re-run on a SECOND unit of this class 2026-09-14)
```

> [!NOTE]
> **§3's install steps have been replayed from scratch on a second unit; §8's measurements have not.**
> The measurements behind this recipe's gates were all taken on one machine — they live, with that
> caveat, in
> [numbers.md §6](../../reference/numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs).

> [!NOTE]
> **There is no image digest to pin, and that is expected.** The upstream recipe *builds* the
> serving image on the node rather than pulling a published one. The reproducibility anchor is
> therefore the upstream commit plus the checkpoint revision, not a registry digest — both are in
> the header above, and **record your own when you clone**, because this recipe moves. Check what a
> node is actually running with:
>
> ```bash
> docker ps --format '{{.Names}} {{.Image}}'
> ```

This is the **second** recipe for this model on this hardware class. The other one
([qwen38-flash-next-180b.md](qwen38-flash-next-180b.md)) runs the GGUF quant on native llama.cpp
with no Docker and no sudo. This one runs the NVFP4 checkpoint on vLLM in a container, and it is
the one to use when **long context** is the point: it is faster on fresh generation, it accepts a
quarter-million-token request, and it stays correct down there.

> **Honest conclusion up front:** this is a **long-context and rewrite engine for a handful of
> users. It is not a shared agent endpoint.** On the identical agentic replay at **~6.5k context**
> it runs at a **fraction of [Qwen3.8-27B + SGLang + DFlash2](qwen38-27b-sglang-nvfp4-dflash2.md),
> and the gap widens under load** — the measured ratios, on one declared basis, are in
> [numbers.md §6](../../reference/numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs).
> At that depth its throughput saturates at about four concurrent sessions while the 27B keeps scaling.
> **How many users it serves depends on how deep they go** — see [§8](#8-what-a-healthy-node-looks-like). Choose
> this model for depth, not for people.

---

## 1. Prerequisites

- A DGX Spark class node, **arm64 / aarch64**. Every image and wheel must be aarch64.
- **Docker**, and your user in the `docker` group — **root-equivalent**, grant it deliberately.
  Undo: `sudo gpasswd -d <admin-user> docker`.
- **~140 GB free disk** for the checkpoint. The PLE table is served from local NVMe at run time, so
  this is not a one-off cost you can reclaim after loading.
- **Room for the serving image on top of that.** ⚠ **Not measured on our node**: we never recorded
  the built image's size, so budget by inference from the sibling SGLang recipe on this hardware
  class, whose serving image runs to tens of GB. Treat it as an allowance, not a measurement.
- **Time for the image build: budget about 15 minutes** with no Docker cache and no base image
  already pulled. ⚠ Upstream's README says "~1 min"; that figure almost certainly excludes the
  base-image pull.
- **The checkpoint must sit on local NVMe.** Network storage is documented upstream to collapse
  prefill on this architecture.
- **The Hugging Face CLI (`hf`).** The checkpoint pull in §3 needs it and it is not part of a base OS
  install. A user-level Python virtualenv with `huggingface_hub` installed into it provides the `hf`
  command without sudo. ⚠ **Install it before §3** — that is the 135 GB step, and discovering the tool
  is missing after starting is the expensive way to find out. This checkpoint needed no access token
  when we pulled it; if yours prompts for one, that is a change at the publisher's end.
- No sudo is required by the upstream scripts or the Dockerfile. We checked; keep checking.

---

## 2. The pinned stack

| component | pin |
|---|---|
| upstream recipe | `blazux/qwen3.8-Flash-DGX`, commit `656cdb0` |
| image | `qwen38-flash-dgx`, built on the node (`sha256:d58dfc39…`, no registry digest) |
| checkpoint | `RadixArk/Qwen3.8-Flash-Next-NVFP4`, revision `7b719225242aacd3dbd3f9407468c2ee9a9d2594`, 206 safetensors, 135.25 GB |
| mode | `MODE=nvfp4` |
| prefix cache | `PREFIX_CACHE=1` |
| deterministic top-k | `DET_TOPK=1` |
| context | `CTX=262144` |
| concurrency cap | `SEQS=8` |
| GPU memory fraction | `GPU_MEM=0.80` |
| speculative decode | `MTP=2` |
| port | `<engine-port>` (**not** 30000 — that is the 27B stack's port) |
| container name | `qwen38-flash` |
| served model id | **`qwen3.8-flash-next`** — lowercase, set by the serve script's `--served-model-name`. Confirmed against a live server's `/v1/models` on 2026-09-09. ⚠ Clients match this string exactly; a capitalised variant is rejected |
| resident footprint | ~76 GiB; host RAM settles at ~17 GiB available |

### Why it fits on one box at all

The model is **125B parameters with ~6B active**, plus a **51B-parameter per-layer / n-gram
embedding (PLE) table** and a **4B MTP head** — ~180B of weights in total. That table is about
**48 GiB in this NVFP4 checkpoint**.

The table is **looked up, never computed**, so it is memory-mapped from NVMe instead of
being made resident. That single fact is the whole recipe. Streaming MoE *experts* from storage is
measured-dead on this hardware class; streaming a lookup table is not.

A consequence worth internalising: **prefill speed depends on the page cache holding that table.**
Leave the box enough free RAM.

### ⚠ Four hard limits

> [!WARNING]
> **Do not raise `GPU_MEM` above 0.80.** Upstream reports 0.85 drifting into swap and 0.875 being
> OOM-killed. The PLE page cache and the model must coexist in the same unified memory.

> [!WARNING]
> **Verify the checkpoint by hash, not by file size.** Upstream's worst reported failure was two
> shards with the **correct byte size and wrong content** — files still being written when a
> download stalled. They load cleanly, report correct shapes, and emit confident nonsense. Hash
> every blob against its own filename before the first boot.

> [!WARNING]
> **Gate a download on completion, never on a size readout.** `du -sh` reports GiB while the model
> card reports decimal GB, so the number you are waiting for never arrives. Gate on the downloader
> exiting, **zero dangling symlinks in the snapshot, and the expected file count (206)** — then the
> hash check.

> [!WARNING]
> **Do not gate on `*.incomplete` files. That test is wrong, and it fails good checkpoints.**
> `huggingface_hub` 1.x names a partial download `<sha256>.<random>.incomplete`, so an orphaned temp
> file never shares a name with the finished blob — counting them answers a different question than
> the one you are asking. On 2026-09-14 a complete, fully hash-verified checkpoint sat next to **16
> orphaned temp files totalling 1.9 GiB**, left behind by download attempts interrupted five days
> earlier. An `INCOMPLETE=0` gate would have rejected a perfectly good checkpoint, and a non-zero
> count says nothing about the blobs that matter. **The completeness test is a dangling-symlink
> count**: the snapshot directory is all symlinks into `blobs/`, so a link with no target is exactly
> a file that did not finish. Orphaned temp files are disk to reclaim, not a fault.

---

## 3. Install

```bash
# 1. Docker group (root-equivalent, grant deliberately)
sudo usermod -aG docker <admin-user>
newgrp docker

# 2. Read the recipe before running it
git clone https://github.com/blazux/qwen3.8-Flash-DGX ~/qwen3.8-Flash-DGX
cd ~/qwen3.8-Flash-DGX
git rev-parse --short HEAD  # <- record this in your own header; ours was 656cdb0

#    What to look for, specifically:
grep -rn "sudo" scripts/ Dockerfile   # expect NOTHING. We checked; keep checking
less scripts/serve.sh                 # the defaults it applies, and the env vars it honours
grep -n "^FROM" Dockerfile            # every base image must be aarch64

# 3. Build the image (no sudo), from the repo root. ~14½ min on a clean node
docker build -t qwen38-flash-dgx .

# 4. Pull the checkpoint (135 GB, 206 files), then verify it by hash before first boot
hf download RadixArk/Qwen3.8-Flash-Next-NVFP4 --revision 7b719225242aacd3dbd3f9407468c2ee9a9d2594
```

> [!WARNING]
> **Build with `docker build -t qwen38-flash-dgx .` from the repo root.** That is what the upstream
> README documents and what produced the image this recipe runs. ⚠ **Do not assume a
> `scripts/build.sh` exists** — run `ls scripts/` and work from what is actually there; the script
> set moves between commits.
>
> ⚠ **This recipe uses `serve.sh` and nothing else.** We did not run `download-weights.sh` (we pull
> with `hf` directly, for the cache reason below) and we did not run `prepare-hybrid.sh` — the
> NVMe-mmap behaviour this recipe depends on came up correctly without it, under the `MODE=nvfp4`
> defaults in the header. **We have not established what `prepare-hybrid.sh` does or when it is
> needed**, only that the configuration documented here works without it. If you are running a
> different mode, read it before assuming the same.

> [!WARNING]
> **Let the download go to the default cache. Do not send it to a `--local-dir`.** `serve.sh`
> resolves the checkpoint through the standard `huggingface_hub` cache, so weights parked in a
> directory of your choosing may not be where the server looks. The cache path is
> `~/.cache/huggingface/hub/models--RadixArk--Qwen3.8-Flash-Next-NVFP4`. **The local-NVMe rule still
> binds** — it is about the PLE table being paged from fast local storage, so if `~/.cache` is not on
> local NVMe, move the cache (`HF_HOME`) rather than the checkpoint.

**Your choice:** `~/qwen3.8-Flash-DGX` is illustrative; put the source wherever you keep source.

Expect the checkpoint pull to dominate wall-clock. Then verify: the blob name **is** the content
hash, so each file can be checked against its own filename. Counts only, never paths:

```bash
REPO=~/.cache/huggingface/hub/models--RadixArk--Qwen3.8-Flash-Next-NVFP4
SNAP=$(echo $REPO/snapshots/*)

CHECKED=0; BAD=0
for f in $(find "$REPO/blobs" -type f -regextype posix-extended -regex '.*/[0-9a-f]{64}'); do
  CHECKED=$((CHECKED + 1))
  [ "$(sha256sum "$f" | cut -d' ' -f1)" = "$(basename "$f")" ] || BAD=$((BAD + 1))
done
echo "CHECKED=$CHECKED BAD=$BAD"
echo "SAFETENSORS=$(find "$SNAP/" -name '*.safetensors' | wc -l)"
echo "DANGLING=$(find "$SNAP/" -xtype l | wc -l)"
```

Expected: `CHECKED=208 BAD=0`, `SAFETENSORS=206`, `DANGLING=0`. **208 against 206 is not a
discrepancy**: the 206 tensor shards plus 2 non-tensor blobs.

Reclaim any orphaned temp files afterwards — they are wasted disk, not a fault:
`find "$REPO/blobs" -name '*.incomplete' -delete`.

**Confirm on the node:** the expected values above are measured — on 2026-09-14, on a second unit of
this class, a completed checkpoint gave `CHECKED=208 BAD=0`, 206 safetensors and `DANGLING=0`. The
block itself is a tidied reconstruction of the checks as run, not a transcript, so read it before you
paste it.

The upstream repo also ships CPU-only unit tests for the two pieces most likely to be silently
wrong — the PLE mmap gather (including its out-of-range guard) and exact deterministic top-k. Run
them; both passed here, and they cost nothing.

---

## 4. The API key

The server binds `0.0.0.0` **with no authentication unless you pass a key.** On a box reachable
from any private network that is not acceptable, so treat the key as part of the install, not an
afterthought.

```bash
umask 077
head -c 32 /dev/urandom | base64 | tr -d '\n' > ~/engine-api-key
export ENGINE_KEY=$(cat ~/engine-api-key)
```

> [!IMPORTANT]
> **Write the key to a file before you use it.** Generated inline into a shell variable, it exists
> only in that shell — a disconnect locks you out of your own running server, with no way to
> recover it short of a restart. Ours came within one dropped connection of exactly that.

Rotation is: write the file, restart the container.

### The alternative: bind loopback and drop the key

If a gateway on the **same box** is the only thing that will ever call this server, publish the
container on loopback instead and run the engine unauthenticated:

```bash
PORT=127.0.0.1:<engine-port> scripts/serve.sh     # note: no EXTRA, so no --api-key
```

`$PORT` is used functionally in exactly one place in `serve.sh` — the `docker run -p` argument — so
this needs no edit to the script.

`$PORT` reaches the `docker run -p` argument and nothing else — **a reading of the script at commit
`656cdb0`, line 119**, not a behavioural test.

**This is a net security gain, not a downgrade.** Before, a shared secret guarded a port open on
every interface; after, the port does not exist off-box at all, and the key stops appearing in
process arguments because there is no key.

**Verify both halves.** `docker port` should report the mapping bound to `127.0.0.1`, an
unauthenticated request from the box itself should return **200**, and the same request from another
machine should fail to connect. **Run a control alongside that last check** — something you know is
reachable on the same host, such as its SSH port — because a bare connection failure proves nothing
on its own.

> [!WARNING]
> **This trades network exposure for local exposure, and the trade must be deliberate.** With no key,
> **any user with a shell account on that box has unattributed access to the model.** That is fine on
> a single-admin node and breaks silently the first time someone is given a login. If shell accounts
> exist or are planned, keep the key.

You would choose this when whatever sits in front of the engine **cannot send an `Authorization`
header** — some routers drive backends from a manifest with no way to attach one, and strip inbound
credentials by design. Keep the generated key file if you may revert; reverting is a restart, not a
rotation.

---

## 5. Serve

```bash
cd ~/qwen3.8-Flash-DGX
export ENGINE_KEY=$(cat ~/engine-api-key)
EXTRA="--api-key $ENGINE_KEY" scripts/serve.sh
```

The `Serve defaults` in the header — `MODE`, `PREFIX_CACHE`, `DET_TOPK`, `PORT`, `CTX`, `SEQS`,
`GPU_MEM`, `MTP` — are **environment variables the serve script reads**, set on the command line
exactly the way `EXTRA` is above:

```bash
SEQS=4 EXTRA="--api-key $ENGINE_KEY" scripts/serve.sh
```

Those defaults are the configuration every number in this recipe was measured on. **Do not tune on
the first run.** `GPU_MEM` in particular has a ceiling — see the hard limits above; do not raise it
past 0.80.

Bind to loopback or a private VPN interface and reach the node over the platform's private network, the same
rule every engine in this folder follows. If this node is not on that network yet, joining it is
§2 of [adding-another-node.md](adding-another-node.md); the hardware-class traps worth reading before
a first build on this silicon are in the [folder README](README.md).

### What a healthy load looks like

**About 11½ minutes** from container start to serving — one measurement of **11 min 24 s**
(2026-09-14, a second unit of this class, **n=1**), timed from the container starting to `/health`
first returning 200.

> [!NOTE]
> **Two clocks exist and they are not the same measurement.** This one is *container start →
> serving*; §11's ~13½ minutes is *reboot → serving*, on a different unit. Do not subtract one from
> the other. **Plan on 15 minutes**, which is why the liveness probe's boot delay is set there.

Watch free host memory rather than the log:

| phase | free host RAM |
|---|---|
| idle, before launch | ~118 GiB |
| weights landing (~76 GiB resident) | falls to ~38 GiB and flattens |
| CUDA graph capture | sawtooth 31-38 GiB — **dips that recover are healthy** |
| KV cache reserved | steps down to **~17 GiB and settles** |

It should never approach single digits. A monitor in a second window is worth the trouble:

```bash
while sleep 10; do echo "$(date +%H:%M:%S) avail=$(free -g | awk 'NR==2{print $7}')"; done
```

---

## 6. The readiness gate

> [!IMPORTANT]
> **Gate on the HTTP status code**, consistent with the rest of this folder.

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<engine-port>/health)" = "200" ]; do
  sleep 15
done
echo FLASH-UP
```

`/health` is **unauthenticated**, so it returns 200 regardless of your key. It proves the model is
loaded, not that auth works. Run the gates below for that.

---

## 7. Verification: three gates, run all three

```bash
# 1. SERVER-UP  : health returns 200
curl -s -o /dev/null -w 'HEALTH=%{http_code}\n' http://127.0.0.1:<engine-port>/health

# 2. AUTH-200   : an authenticated request is accepted.
#    Put the key in a mode-600 curl config, never in the command line.
umask 077
printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/engine-api-key)" > ~/.engine-curlauth
curl -s -o /dev/null -w 'AUTH=%{http_code}\n' -K ~/.engine-curlauth \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"hi"}],"max_tokens":1024}' \
  http://127.0.0.1:<engine-port>/v1/chat/completions

# 3. NOAUTH-401 : an unauthenticated request is rejected
curl -s -o /dev/null -w 'NOAUTH=%{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.8-flash-next","messages":[{"role":"user","content":"hi"}],"max_tokens":1024}' \
  http://127.0.0.1:<engine-port>/v1/chat/completions
```

Expected: `HEALTH=200`, `AUTH=200`, `NOAUTH=401`. Verdict words and status codes only, no key and
no path in the output.

⚠ **If you took §4's loopback-and-no-key posture, gates 2 and 3 do not apply as written.** With no
key configured there is nothing to authenticate against: gate 2's header is ignored and gate 3
returns **200**, not 401 — that is the posture working, not a broken install, and it is not a reason
to re-add a key you deliberately removed. On that branch run gate 1, then §4's own two checks: the
`docker port` mapping must be bound to `127.0.0.1`, and the same request from another machine must
fail to connect. Remove the auth file when you are done: `rm -f ~/.engine-curlauth`.

> [!IMPORTANT]
> **`-H "Authorization: Bearer $(cat keyfile)"` puts your key in the process table.** The shell
> expands the substitution *before* `curl` is executed, so the live secret becomes a command-line
> argument and any user on the box can read it out of `ps` for as long as the request runs.
> **`printf` is a shell builtin**, so writing the key into a mode-600 config file forks no process and
> exposes no argument, and `curl -K` reads the header from there. Use this pattern for every
> key-bearing call you make by hand.
>
> ⚠ **A keyed server is a deliberate exception to that rule, not an oversight.** `--api-key` on a
> launch command, or in a systemd `ExecStart`, is visible in `ps` for the entire life of the
> service — far longer than any request. It is accepted because the alternatives are worse: an engine
> that reads a key from a file at startup is not a feature these servers offer, and the realistic
> choice is between a key in the process table and no key at all. **If that exposure is not
> acceptable on your box, use the loopback posture in §4 instead** and remove the key entirely.
>
> ⚠ One correction to a plausible-sounding worry: bash's job-control `Done` lines print a command
> **as parsed**, so an unexpanded `$K` stays unexpanded there. That is not the leak. `ps` is.

> [!WARNING]
> **Upstream's own `scripts/smoke-test.sh` fails when you set an API key.** It does not send the
> key, gets a 401, and dies on `KeyError: 'choices'`. The health step still passes because that
> endpoint is unauthenticated. **This is not a fault in the stack.** Use the gates above instead.

Note the `max_tokens` value in those gates is 1024, not 16, deliberately — see the budget warning in
[§8](#8-what-a-healthy-node-looks-like).

---

## 8. What a healthy node looks like

**This section is about *your* node, not about ours.** It gives you the gates to check a fresh build
against, and the readouts on this stack that will lie to you if you trust them.

**Every measurement behind these gates — the depth curve, the concurrency ladders at four depths, the
capacity envelope, the correctness probe and the interference test — lives in
[numbers.md §6](../../reference/numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs),
with its n, its workload and its depth attached.** It is not repeated here — a figure without the caveat
beside it is how a range becomes a promise.

### ⚠ Read this before measuring anything yourself

> [!WARNING]
> **This build withholds streamed output and releases it in a fixed ~19-second tail.** The streamed
> window measured close to nineteen seconds at every depth we tried, **independent of how long
> generation actually took** (the four per-depth values are in
> [numbers.md §6](../../reference/numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs)). Every metric derived from stream timestamps
> (TTFT, per-token rate) is therefore wrong on this stack, **in the optimistic direction**: our
> harness reported "95 t/s" and "431 t/s" for a box whose real rate is ~35.
>
> **Measure by wall-clock differencing instead:** send the same prompt twice, once with a tiny
> `max_tokens` (that run is prefill alone) and once with a long output, then
> `decode = Δtokens / Δwall`. Take token counts from the server's `usage` field, never from counted
> chunks. Anyone who benchmarks this recipe with streaming timestamps will publish inflated numbers.

> [!WARNING]
> **Two more readouts on this stack that are not measurements.**
>
> 1. **`Avg generation throughput` in the server log is a health indicator, not a measurement.** It
>    is a short (5-10 s) windowed average, so it swings with speculative-draft acceptance — we
>    watched acceptance move 32% → 45% inside a single request. And on a speculative-decoding build
>    this counter is known upstream to over-count, because it assumes every drafted token was
>    emitted. **Read it as "is it moving?" and nothing more.**
>    ⚠ **Do not try to quantify the error** by setting the logged rate against an end-to-end one:
>    a generation-window rate and a whole-request rate are not the same measurement. The size of the
>    discrepancy is unmeasured; only its direction and its causes are established.
> 2. **`GPU KV cache usage %` is not a token counter.** It includes retained prefix-cache blocks, so
>    4.0% read as ~20.5k tokens on a request that had generated about 8k. Do not derive progress or
>    context depth from it.
>
> The general form, which is the part worth keeping: **a generation is finished when the response
> says so.** Do not infer completion from a throughput line and a wall clock. A run that needs 9m11
> is indistinguishable from a wedged one at 5m30, and the only thing that tells them apart is the
> response arriving.

### Acceptance gates for a new node

Bands, not measurements: each is drawn wider than the spread of the run it came from, so a healthy
node passes without you having to reproduce our apparatus. **If a gate fails, the note beside it is
where to look first.** All of them assume the shipped configuration in the header.

| check on your node | expect | if it misses |
|---|---|---|
| decode at 8k-128k, **single stream**, **wall-clock differenced** | **28-40 t/s** | check the PLE table is warm in page cache — free host RAM should have settled, not still be falling. ⚠ A *loaded* node is not measured by this gate: eight clients at ~33k get ~4 t/s each and that is healthy |
| prefill, 32k-128k | **~1,900-2,400 t/s** | almost always the page cache again, or the checkpoint not on local NVMe |
| time to first token at 128k | **50-75 s** | as above |
| KV pool in the startup log, at the shipped memory fraction (command below) | **~500k tokens** | materially lower means either a different memory fraction or another process resident on the box |
| eight clients at ~33k context | **6-8 admitted, KV above ~85%, no preemption, no errors** | fewer admitted is normal at greater depth, and normal for single-turn traffic at any depth — confirm both before worrying |
| needle at 50% depth, **`max_tokens` ≥ 1024** | correct at every depth (see below for a runnable version) | see the budget warning below before blaming the server |

⚠ **The bands are deliberately wider than our own measured spread**, because the cells behind them
used draftable output at depth (a ceiling) and because occupancy is workload-shaped — single-turn
traffic admits fewer clients than multi-turn at the same depth, and both are healthy.

**Running the retrieval gate without our harness.** Two of the gates above — the eight-client one and
this — were measured with a load harness that is not in this repo. The retrieval one does not need it:
build a prompt of filler text with a distinctive fact planted halfway through, then ask for the fact.

```text
<~N tokens of filler>
The reference identifier for this document is QX-4417.
<~N tokens of filler>

What is the reference identifier for this document? Answer with the identifier only.
```

Vary N to reach the depth you want, use **independent filler per request** so two requests do not
share a prefix, and send `max_tokens: 2048`. **A correct answer proves retrieval at that depth; a
wrong one proves nothing** until you have ruled out the budget — see the warning below. For the
eight-client gate, any tool that can hold N chat completions open at once will do.

**Reading the KV pool without leaking your key:**

```bash
docker logs qwen38-flash 2>&1 | grep -oE 'GPU KV cache size: [0-9,]* tokens'
```

> [!WARNING]
> **Use `grep -o` with that exact field, never a bare `grep` or `tail` on this log.** The server dumps
> every launch argument onto a single startup line — **including `--api-key` if you set one in §4**.
> `grep -o` prints only the matched field, so the key cannot come with it.

### The sizing conclusions, without the tables

- **How many users fit depends on how deep they go, and "saturates at four" is a shallow-context
  property.** At ~6.5k, four and eight sessions are indistinguishable. At ~33k, eight return
  materially more than four. **Never quote a saturation figure without the depth it was measured at.**
- **Keep per-user context under about 35k.** Past it, aggregate throughput falls sharply and stays
  there. ⚠ **A planning boundary, not a measured threshold** — the deep cells behind it are single
  runs.
- **The KV pool is a property of your configuration, not of the model** — the memory fraction, the
  checkpoint, the box. Read your own with the command in the gate table above; ours reads ~514k.
- **The server throttles admission rather than failing.** As depth rises it runs fewer requests at
  once and the excess waits. **No preemption at any depth, and no request failed.**
- **Retrieval stayed correct under load** at ~33k with the pool nearly full. ⚠ Single-turn only;
  correctness of prefix reuse *across* turns has not been inspected.
- **★ One deep user stalls everyone else.** A single client submitting a large document costs
  everyone else well over half their rate for as long as that prefill runs — and the worst-hit
  sessions roughly four fifths — while the deep client barely notices. **The asymmetry is the finding**, and it is about one request's prefill size
  rather than how many sessions are in flight. ⇒ **Every concurrency expectation above holds only if
  nobody submits a large document** — which on a model chosen *for* large documents is not a safe
  assumption. An upstream mitigation exists — chunking the long prefill so decoders interleave — but
  **we have not established which knob governs it on this build.** The two candidates are
  `--long-prefill-token-threshold` (suggested 1024) and `max_num_batched_tokens` (8192 here); the
  sources we have disagree about which one is the lever, and **neither has been tested**. Settle that before
  quoting a flag to anyone. Either needs a restart, so bundle it with one you already need.

> [!WARNING]
> **★ If you re-measure retrieval at depth, give the probe a real `max_tokens` budget — at least
> 1024.** Run at a 16-token cap, the same eight-wide test returned **0/16**, and none of it was a
> retrieval failure: the entire budget went to reasoning before the answer arrived, and because
> 16-token generations finish at the end of prefill the sessions never actually overlapped, so the
> box was never loaded either. **The gate is asymmetric — a correct answer proves retrieval, a wrong
> one proves nothing.** A same-day single-stream control at the same cap also missed, which is what
> identified the probe rather than the server as the fault.

### Health covariates

Across the whole measurement campaign clocks held at 2411 MHz and 40 °C with no throttle event, and
host RAM available settled at **16-17 GiB** and stayed there. **A falling free-RAM figure is the
signal to watch**, not the clock.

**How this stack compares against published figures for it** is in
[numbers.md §6](../../reference/numbers.md#against-published-claims-for-this-stack) — two independent
third-party apparatus both measure decode **below** our band, and our prefill sits below theirs.

---

## 9. Handing this endpoint to users

Once anyone but you calls this server, four things have to reach them. **The client-side rules
themselves are not repeated here** — they live in [connecting-clients.md §5](../../connecting-clients.md#5-getting-good-results),
which is the page you hand over, and a second copy of them here would drift out of step with the one
people actually read.

1. **Give them [connecting-clients.md](../../connecting-clients.md)**, and point at §5 specifically: deriving `max_tokens`
   from context minus prompt, and `max_retries = 0` with a client timeout of at least 1800 s. Those
   two account for most support requests on this stack.
2. **Tell them the first question about a big document is slow and the rest are fast.** It is the
   single most useful sentence you can give a user of this model, because the first timing they
   experience is the worst one they will ever see.
3. **Tell them their client's tokens/sec and time-to-first-token displays are wrong here**, because
   of the streaming tail described in §8. Text arriving in one burst at the end is this build's
   normal behaviour, not a hang.
4. **Their timeout has to clear the longest session you have actually observed.** Ours was **948 s**,
   which is why the recommendation is 1800 s rather than the 600 s an SDK defaults to. ⚠ We ran with
   1800 s throughout, so **we cannot say whether 600 s would have held.**

> [!IMPORTANT]
> **The budget trap is worth stating to users in your own words, because it does not look like a
> budget problem.** This is a reasoning model: too small a `max_tokens` does not truncate the answer,
> it **deletes** it — the whole budget goes to thinking and the content comes back empty. A wrong or
> empty answer from this model is a budget symptom until the budget has been ruled out, and that
> applies to your own monitoring as much as to their code (§8).

---

## 10. Where this model fits

- **Long documents and long context**, with retrieval verified correct at every depth we tested and
  decode that does not degrade until beyond 128k.
- **Repeated questions about the same document**, which is where this stack is at its best: a
  follow-up over context the server has already read comes back roughly an order of magnitude faster.
  Tell users *the first question is slow, the ones after it are fast* — the first timing they
  experience is the worst one they will get.
- **Rewrites and refactors**, where much of the answer already exists in the context.
- **A handful of users, and the number depends on their context depth** — see §8 and
  [numbers.md §6](../../reference/numbers.md#6-long-context-the-depth-curve-and-what-sharing-it-costs).
- **Not** the agent driver and **not** a busy shared endpoint: **one deep request stalls the rest**,
  which is the failure that actually decides it.

⚠ **We have not established that this model is "smarter" than the 27B**, and the only controlled
head-to-head we know of favours the 27B. Do not repeat the "bigger model, therefore better answers"
pitch without a test behind it — see
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md).

## 11. Teardown

```bash
docker rm -f qwen38-flash
docker rmi qwen38-flash-dgx
rm -rf ~/qwen3.8-Flash-DGX
# optional, ~135 GB: remove the checkpoint from the HF cache
```

Nothing is installed on the host: no systemd unit, no packages, no sudo beyond the Docker group.

> [!WARNING]
> **This hardware class has cut its own power once, and the cause is unexplained.** It happened
> immediately after a stop released ~60 GiB, with no kernel trace of any kind. That timing is an
> observation, not a mechanism: the vendor's acknowledged trigger is **sustained GPU workload**,
> not a teardown, so we draw no rule from it and you should not either. Know that it can happen.
> See **Hard power-off, cause unexplained** in the [folder README](README.md).

### ★ It already restarts itself, and we tested that

The `docker run` line carries **`--restart unless-stopped`**, so Docker brings the container back
when its daemon starts — including after a reboot or a power event — provided `docker` itself is
enabled at boot. **Verified**: a deliberate `sudo reboot` with nothing started by hand had `/health` returning 200
about 13½ minutes later. ⚠ **One event, and the split between boot and weight
load was not measured** — do not decompose the 13½ minutes; see the note in §5.

Check yours: `systemctl is-enabled docker` should say `enabled`, and
`docker inspect qwen38-flash --format '{{.HostConfig.RestartPolicy.Name}}'` should say
`unless-stopped`.

> [!WARNING]
> **A restart policy handles a container that *exits*. It does nothing about one that is alive and
> no longer serving**, and we have demonstrated that on demand: `docker pause` leaves the container
> reporting **`Up … (Paused)`** with its **port still bound**, and the policy never fires because
> nothing exited. A `docker ps` check passes. A port check passes. **Only a real completion catches
> it.** If this endpoint matters to other people, it needs a liveness probe — the user-unit and
> timer pattern is in
> [durable-gateway-user-service.md](../gateway/durable-gateway-user-service.md).

### ★ A generic liveness probe is wrong on this model

If this endpoint matters to other people it needs a probe that makes a **real completion** — a port
check and `docker ps` both pass against a wedged server. Three of its settings are engine-specific
here and the generic defaults get all three wrong: ask for **`max_tokens` 2048** (a tight cap returns
empty content from a healthy server), use a **300 s timeout with several consecutive failures**
(a busy box looks exactly like a wedged one), and set the **boot delay to 15 minutes**. Have it log
every failure and write nothing on success, so an empty log is the healthy state, and consider
leaving the restart action off until people actually depend on the endpoint. The pattern, the unit
files and the break-tests are in
[durable-gateway-user-service.md](../gateway/durable-gateway-user-service.md).

---

**See also.** The conventions these figures were taken under — and the reason the streaming caveat
above matters to anyone re-measuring — live in
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md); the cross-stack table
they belong in is [numbers.md](../../reference/numbers.md). Both are worth reading before this model
is set against the 27B, which
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md) does directly. If the
one-to-four users are other people, they should reach it through
[the gateway runbooks](../gateway/README.md) rather than the port.
