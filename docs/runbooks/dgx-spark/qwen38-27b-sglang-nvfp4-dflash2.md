# Qwen3.8-27B on a DGX Spark: SGLang + NVFP4 + DFlash2

```text
Verified against : DGX Spark class node (NVIDIA GB10, sm_121, arm64, 128 GB unified)
Engine / build   : SGLang v1.5.9. The installer pulls a published SGLang base image
                   (digest `febfb971…`) and then BUILDS the serving image from it on the
                   node, so the derived local tag carries no digest of its own.
Model            : RadixArk/Qwen3.8-27B-NVFP4, revision 52d1adc
Drafter          : z-lab/Qwen3.8-27B-DFlash2 (DFlash2, draft length 8)
Date verified    : 2026-09-07 (stack first stood up and measured 2026-09-01)
```

> [!NOTE]
> **The digest to pin is the base image's, not the serving image's.** The installer pulls a
> published SGLang base image — **digest `febfb971…`, the digest to match across nodes** — and then
> *builds* the serving image from it on the node, so `docker images --digests` reports
> `DIGEST: <none>` for the derived tag. **The reproducibility anchor is the installer version plus
> that base digest**; pin both (recipe v1.5.9, commit `17e7e22`) and the local tag follows.
> Check what a node is actually running with:
>
> ```bash
> docker ps --format '{{.Names}} {{.Image}}'
> ```

The best agentic stack we have measured on this hardware class — **~35-41 t/s on agentic replay
with time-to-first-token of 0.14-0.41 s on every turn**. The cross-stack comparison that places it is
in [numbers.md](../../reference/numbers.md).

The install uses a public installer, `hasso5703/dgx-spark-qwen38` at recipe v1.5.9, commit
`17e7e22`, which pins the image, model, drafter and serve flags. Everything below that is our own:
the verification gates, the ops notes and the measured numbers.

> [!NOTE]
> Check the repository for a newer recipe version before installing, and read the installer before
> running it. It clones the repo and executes an install script that needs sudo for the systemd
> units.

---

## 1. Prerequisites

- A DGX Spark class node, **arm64 / aarch64**. Every image and wheel must be aarch64; x86 images
  will not run.
- **Docker**, and your user in the `docker` group. On a single-user box this is standard Docker
  post-install, and it is **root-equivalent**: anyone in that group can mount the host filesystem
  into a container. Grant it deliberately. Undo: `sudo gpasswd -d <admin-user> docker`.
- ~60 GB free disk for the image, target checkpoint and drafter.
- `sudo` is needed only to install and enable the systemd units.

**Inspect before you run.** This is a `curl | bash` style installer. We read its bootstrap script
(which clones the repo and executes the installer), enumerated every `sudo` line in the installer,
and verified the memory caps in the unit template before executing. Keep that habit.

---

## 2. The pinned stack

| component | pin |
|---|---|
| base image | published SGLang base, **digest `febfb971…`** — the digest to match across nodes |
| serving image | built on the node from that base by the installer; a local tag only, no registry digest |
| target model | `RadixArk/Qwen3.8-27B-NVFP4`, revision `52d1adc` |
| drafter | `z-lab/Qwen3.8-27B-DFlash2` |
| spec decode | `--speculative-algorithm dflash2 --spec-draft-length 8` |
| memory | `--mem-fraction-static 0.50` |
| context | `--context-length 262144` |
| concurrency cap | `--max-running-requests 8` |
| autotune | `--disable-flashinfer-autotune` |
| KV dtype | **bf16 (default). Do not set fp8.** |
| Docker hard caps | `--memory 100g --memory-swap 100g --shm-size 16g` |
| ports | `30000` API (key-authed), `30001` keepalive proxy |
| served model id | `qwen38` (confirmed against the live server: the concurrency harness sent exactly this string) |
| systemd units | `qwen38-sglang`, `qwen38-keepalive`, both enabled at boot |

Optional hygiene: checkpoint revision `319f741` fixes some quant config metadata versus `52d1adc`
and measured ~0% speed difference. Either is fine; pin one.

### ⚠ Two hard limits

> [!WARNING]
> **Never raise `--mem-fraction-static` above 0.50 outside the documented Docker caps.** The
> upstream recipe documents a host freeze in which SSH dies and only a power cycle clears it, and it
> names the cause: **SGLang's memory accounting on this chip misses 25-40 GB.** The unified memory
> architecture means the engine can starve the host, not just itself. Third-party configurations
> circulating at `0.80` and `0.90` do exist; **we have not run either, and we do not copy them.**
> Memory fraction and draft budget are independent levers, so do not copy a higher fraction from
> someone else's throughput result.

> [!WARNING]
> **fp8 KV cache plus DFlash2 produces infinite repetition.** This is a structural incompatibility,
> documented upstream. We closed it on that evidence and never ran an fp8 KV arm here, so the rule
> is inherited, not reproduced. KV stays bf16. On GB10 the dequant cliff makes quantized KV a net
> loss anyway.

---

## 3. Install

```bash
# 1. Docker group (root-equivalent, grant deliberately)
sudo usermod -aG docker <admin-user>
newgrp docker            # or log out and back in

# 2. Read the installer before running it
git clone https://github.com/hasso5703/dgx-spark-qwen38 ~/dgx-spark-qwen38
less ~/dgx-spark-qwen38/install.sh   # enumerate the sudo lines, check the unit template caps

# 3. Run it. Pulls the pinned image, the NVFP4 target and the DFlash2 drafter,
#    writes ~/.config/qwen38/api-key, installs and enables the systemd units.
bash ~/dgx-spark-qwen38/install.sh
```

Upstream also offers a one-line `get.sh` that clones and execs `install.sh` for you. Clone and read
first instead: the install step needs sudo to place systemd units, so it is worth seeing what it
does. **Your choice:** the destination `~/dgx-spark-qwen38` is illustrative, put it wherever you
keep source. Check upstream for a newer recipe version before installing.

Expect a long first run: the image and ~20 GB of weights come down over the HF CDN, whose
throughput swings widely. That is normal; do not reach for a parallel downloader.

---

## 4. The API key

The real key lives in **`~/.config/qwen38/api-key`**, mode `600`.

> [!IMPORTANT]
> The installer also writes an `opencode.json` containing a **different** value. The server rejects
> it with 401. Their own launcher path works, but **any raw client must read the key file.** If you
> get a 401 while using "the key from the config", this is why.

The systemd unit passes the key as `--api-key "$(cat ~/.config/qwen38/api-key)"`, so:

**Rotation = write the file, restart the unit.** Nothing else. No unit edit, no re-install.

```bash
# rotate (never echo the key to a terminal or a log)
umask 077
cp ~/.config/qwen38/api-key ~/.config/qwen38/api-key.old   # ★ FIRST: `>` truncates in place
head -c 32 /dev/urandom | base64 | tr -d '\n' > ~/.config/qwen38/api-key
sudo systemctl restart qwen38-sglang
```

Then re-run the three verification gates below, and **`shred -u ~/.config/qwen38/api-key.old`
once the new key is confirmed working**. Without that copy there is no rollback — the old value is
unrecoverable the instant the redirect runs.

⚠ **A gateway in front of this node holds its own copy of this key**, re-read only at load time, so
rotating here breaks every consumer until the gateway's `.env` is updated and it is restarted.
Neither node gate will show you that — see
[the gateway recipe](../gateway/litellm-gateway-and-api-keys.md#2-secrets).

### ⚠ Repairing the unit file: rebuild, do not regex-edit

Regex-editing the `--api-key` line in the generated unit is how this service gets taken down — the
line holds a command substitution, and a pattern that stops at the first space beheads it. The reliable pattern is to **regenerate the unit
from the installer's own template**, re-rendering it with your home directory substituted, rather
than patching the live file in place. ⚠ There is no canned script for this in the repo — write a short
one against whatever template your installer shipped. An installer-generated unit is derived
data. Treat it as such.

---

## 5. Serve flags (for a hand-rolled unit)

If you write your own unit rather than using the installer's, this is the shape. Keep the Docker
caps and the memory fraction together; they are one safety mechanism, not two options.

```bash
docker run --rm --name qwen38-sglang \
  --gpus all --network host --ipc host \
  --memory 100g --memory-swap 100g --shm-size 16g \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  <local-image-tag> \
  python3 -m sglang.launch_server \
    --model-path RadixArk/Qwen3.8-27B-NVFP4 --revision 52d1adc \
    --speculative-algorithm dflash2 \
    --speculative-draft-model-path z-lab/Qwen3.8-27B-DFlash2 \
    --spec-draft-length 8 \
    --mem-fraction-static 0.50 \
    --context-length 262144 \
    --max-running-requests 8 \
    --disable-flashinfer-autotune \
    --served-model-name qwen38 \
    --host 127.0.0.1 --port 30000 \
    --api-key "$(cat ~/.config/qwen38/api-key)"
```

**Confirm on the node:** `<local-image-tag>` is whatever the installer built on *your* machine; we
never recorded ours. Read it off the running container or the image list, then write it back into
your own copy of this recipe:

```bash
docker ps --format '{{.Names}} {{.Image}}'
docker images --digests
```

> [!NOTE]
> **This command line was assembled by hand from individually sourced flags. We have not run it
> verbatim.** The flags and their values are the pinned ones above and each is sourced, but the
> invocation as a whole is untested; the installer's generated unit is what we actually ran. Treat
> it as a starting shape to check, not a known-good paste.

The image tag is the one the installer builds. Confirm what a given node actually runs with
`docker ps --format '{{.Names}} {{.Image}}'`, since the tag changes with the installer version.

Bind `127.0.0.1` and reach the node over the platform's private network or a tunnel, the same rule
every engine on the platform follows.

```bash
sudo systemctl enable --now qwen38-sglang qwen38-keepalive
```

---

## 6. The readiness gate

> [!IMPORTANT]
> **Gate on the HTTP status code. Never on the body, never on the port.**
>
> SGLang's `/health` returns **200 with an EMPTY body**, so a body grep spins forever. And a port
> check is wrong for the opposite reason on other engines (llama-server binds its port before the
> model loads and answers 503 until ready).

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:30000/health)" = "200" ]; do
  sleep 5
done
echo SERVER-UP
```

**Restart behaviour to expect:** for roughly 5-7 minutes after `systemctl restart`, the unit shows
`active` while the health check returns `000` (port not yet bound, model still loading). That is
normal, not a failure.

---

## 7. Verification: three gates, run all three

```bash
# 1. SERVER-UP  : health returns 200
curl -s -o /dev/null -w 'HEALTH=%{http_code}\n' http://127.0.0.1:30000/health

# 2. AUTH-200   : an authenticated request is accepted.
#    printf is a shell builtin, so the key never becomes a process argument
umask 077
printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/.config/qwen38/api-key)" > ~/.q38-curlauth
curl -s -o /dev/null -w 'AUTH=%{http_code}\n' -K ~/.q38-curlauth \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  http://127.0.0.1:30000/v1/chat/completions

# 3. NOAUTH-401 : an unauthenticated request is rejected
curl -s -o /dev/null -w 'NOAUTH=%{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  http://127.0.0.1:30000/v1/chat/completions
```

Clean up afterwards: `rm -f ~/.q38-curlauth`.

> [!IMPORTANT]
> **Never pass a live key with `-H "Authorization: Bearer $(cat keyfile)"`.** The shell expands the
> substitution before `curl` runs, so the secret becomes a command-line argument that any local user
> can read out of `ps` for the life of the request. `printf` is a builtin — it forks nothing and
> exposes nothing — and `curl -K` reads the header out of a mode-600 file. Apply this to every
> key-bearing call in this repo.

Expected: `HEALTH=200`, `AUTH=200`, `NOAUTH=401`. These commands print verdict words and status
codes only, with no key and no path. Keep them that way when you paste output anywhere.

The `"model"` value `qwen38` is confirmed against the live server: the concurrency harness sent
exactly that string and it was accepted.

A fourth, softer gate worth running once: let a real agent complete a small task end to end. Our
screen was a wordfreq implementation scored against the plain assertion tests the agent wrote itself
(passed 5/5, and 6/6 on a later re-run), plus an adversarial dependency-resolver probe scored 9/9 on
held-back tests, including a cycle-membership trap. ⚠ **A completion gate is a screen, not a certification.** No perplexity or KL comparison of
NVFP4 against the GGUF Q4 quant has been run.

---

## 8. Measured numbers

**Single stream.** Our agentic replay is a captured 5-turn real coding session, replayed
byte-identically, plus chat-instrument code and prose cells, thinking on, n=3.

| workload | measured |
|---|---|
| agentic replay, pooled decode | **~35-41 t/s** (day/composition band, see below) |
| code generation, 512-tok cells | **42-50 t/s** |
| prose, 512-tok cells | **~24-25 t/s** |
| TTFT, warm turns | **0.14-0.41 s on every turn** (radix prefix cache) |
| spec-decode acceptance | ~4.0 of 8 on agentic content; **pure code not measured on this stack** (the ~64.6% figure elsewhere in this repo is the llama.cpp GGUF stack's, 208/322 — a different engine and quantisation) |

⚠ **Quote the agentic figure as a range.** Identical config and identical task measured ~40.8
pooled on one day and ~35.5-37.7 on another. A fresh-server control after a restart ruled out
uptime and cache state; the residual is acceptance and content variance. Any claim under 10%
needs same-day interleaved controls. If the first measurement of a session comes in below ~38,
do a behavioural clock check under load before concluding anything.

**Concurrency.** Closed-loop agentic sessions, each with a unique preamble so the radix tree forks
at token 0; without that, sessions share KV and every rung is fiction. Context ~6.5k.

| in-flight sessions | per-stream decode | aggregate, steady state | aggregate, full window |
|---|---|---|---|
| 1 | ~33-36 t/s | ~34 | ~34 |
| 4 | ~33 t/s | ~98 | ~86 |
| **8** | **~24-27 t/s** | **~134-188 (median ~162)** | ~115-125 |

- Scaling efficiency at c=8 is **57%**, i.e. 4.56× throughput for 8× concurrency. Note the base:
  that 4.56× is against the **per-stream decode rate at one session (~35.6 t/s)**, not against the
  c=1 *aggregate* of 34, which would read 4.76×. A completely
  different instrument (8 short prompts, 256-token caps) measured 161.7 aggregate on the same
  server: two instruments, same figure.
- Health during the ladder: **0 retractions, 0 errors across 88 sessions**, clocks 2385-2418 MHz
  with `clocks_event_reasons.active = 0x0` on all 480 samples.
- ⚠ **Scope: measured at ~6.5k context.** Repo-worker traces run 20-30k+, which is pool-bound.
  See the table below: ~32k context fits about 6 by pool arithmetic, but that is a **ceiling with no
  occupancy allowance**, and this stack has never been measured at that depth — **plan below 6 and verify on your own server** ([numbers.md §3](../../reference/numbers.md#3-context-and-capacity)).
  Never quote the c=8 figure
  next to a repo-worker claim without that sentence. c=16 is untested; raising
  `--max-running-requests` requires a restart which may resize the pools, so the whole ladder must
  be re-run under that boot, never spliced.

### KV pool and context budget

**Read your own pool from the server's own startup log**, never from configuration:

```bash
docker logs qwen38-sglang 2>&1 | grep -o 'max_total_num_tokens=[0-9]*'
```

> [!WARNING]
> **Use `grep -o` with the exact field, never a bare `grep`.** This server dumps every launch
> argument — **including `--api-key`** — onto a single startup line that matches almost any keyword.
> `grep -o` prints only the matched field and cannot emit the key; a bare `grep` prints the whole line.

**Measured on this config: `max_total_num_tokens` = 196,761 tokens** at mem-fraction 0.50. The
model is hybrid, and only 16 of its 64 layers grow KV with length, giving ~32 KiB/token, which
reproduces the pool figure exactly. ⚠ **That derivation assumes an fp8 KV cache while this stack
serves bf16, and the two have never been reconciled.** Quote the measured pool figure freely; before
reusing the per-token arithmetic, re-derive it against a running server. The drafter holds its own
bf16 KV (+~3.76 GB), and the mamba state pool is 96 slots.

| per-request context | max concurrent (pool-bound, zero cache reuse) |
|---|---|
| 8k | 24 by pool, but `--max-running-requests 8` binds first |
| 32k | **~6** |
| 64k | **~3** |
| 128k | **1** (one request = two thirds of the pool) |
| single request ceiling | **~196k. The pool, not the 262,144 flag, is the limit** |

**Overflow behaviour:** an over-limit request returns a clean HTTP 400. A pool exhausted *during*
decode causes a **retraction**: the request silently stalls and later re-prefills from scratch, a
latency spike with no error. There is also a documented "fake dead" hang after pool-full upstream,
so ops monitoring should treat health-check timeouts as a restart trigger.

Note that projects with different system prompts share nothing and evict each other's radix
prefixes; agents sharing a system prompt hit 75-95% cache reuse.

---

## 9. Selecting reasoning effort per request

Qwen3.8 **defaults to `xhigh` reasoning effort.** All of our headline measurements ran at that
default. Effort is selected per request, client-side, and the server is untouched:

```json
{
  "model": "qwen38",
  "messages": [{ "role": "user", "content": "..." }],
  "chat_template_kwargs": { "reasoning_effort": "low" },
  "max_tokens": 8192
}
```

**Use low for worker agents, and request xhigh per-request for work someone has flagged as hard.**
Low effort cuts generated tokens by more than half on multi-turn agentic work and is worth close to
nothing on a single question, so it is an **agent-loop lever, not a per-request one**. It passed the
same quality screens xhigh passes, tying 72/72 on a nine-run head-to-head. ⚠ **Size the gain at
~1.65× under load, not the 3× that slot-occupancy arithmetic suggests.** The full effect table, the
workload each row was measured on, and why the 3× reasoning fails are in [numbers.md §4](../../reference/numbers.md#4-reasoning-effort-an-agent-loop-lever-not-a-per-request-one).

`chat_template_kwargs` **is forwarded unmodified through a LiteLLM gateway**, with no
`allowed_openai_params` needed — though LiteLLM *does* block a bare `reasoning_effort` parameter, so
do not assume from one to the other. See
[litellm-gateway-and-api-keys.md](../gateway/litellm-gateway-and-api-keys.md).

---

## 10. Handing this endpoint to users

**The general client-side rules are not repeated here.** They live in
[connecting-clients.md §5](../../connecting-clients.md#5-getting-good-results) — deriving `max_tokens` from context minus
prompt, `max_retries = 0` with a timeout of at least 1800 s, and taking token counts from `usage`
rather than from streamed chunks. Hand that page over and point at §5.

Two things specific to this stack that connecting-clients.md does not carry:

- **Set a runaway guard as well as a floor.** Our replay harness caps at 8192. We have watched a
  runaway generation of 30.8k tokens — on a different model, engine *and* hardware class (Gemma 4 on
  llama.cpp, Strix Halo), which is why it belongs in every serving recipe rather than being a
  property of this one. We have also measured both arms of a test hitting a 4096 cap and censoring
  the result, so the cap needs room at both ends.
- **Speculative decoding is why that token-counting rule bites hardest here**: a single streamed
  chunk can carry several tokens, so chunk counting undercounts badly on this stack rather than
  slightly.

---

## 11. Tuning: closed

**Single-box rate tuning for this model on this stack is closed. Do not re-tune it.** Draft budgets
above 8, a calibrated NVFP4 drafter and CPU pinning were **measured** and came back null or worse —
the arms, the numbers and the verdicts are in
[tested-and-rejected.md](../../reference/tested-and-rejected.md).

⚠ The two settings that are **not** tuning knobs are in the hard limits above: `--mem-fraction-static`
stays at 0.50, and the KV cache stays bf16.

## 12. Teardown

```bash
sudo systemctl disable --now qwen38-sglang qwen38-keepalive
sudo rm /etc/systemd/system/qwen38-*.service
sudo systemctl daemon-reload
docker image rm <local-image-tag>
sudo gpasswd -d <admin-user> docker      # only if the group was added for this
```

**Confirm on the node:** both the local tag and the space it reclaims are yours to read off the
machine. Check `docker images` for the exact tag and its size before removing anything.

---

**See also.** [how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md) carries
the conventions behind every figure on this page — the replay instrument, the day band, what
"measured" is allowed to mean — and [numbers.md](../../reference/numbers.md) is the cross-stack
table these cells belong to. For the choice between this stack and the others on the same node, see
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md). To hand a user a
key instead of a shell on the node, put the gateway in front:
[the gateway runbooks](../gateway/README.md).
