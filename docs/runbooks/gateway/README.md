# Gateway

## What it is

One OpenAI-compatible HTTP endpoint sitting in front of one or more backend inference nodes.

A user gets the same three values a hosted provider gives them (`base_url`, `api_key` and
`model`) and points any OpenAI SDK or OpenAI-compatible client at it. They never need to know which
physical machine answered, never need an SSH tunnel of their own, and never need a login on a node.

Behind it, the gateway:

- maps a public `model_name` onto one or more backends by `api_base`;
- authenticates requests against a master key and scoped per-consumer keys, where a consumer is
  whoever you chose to name a key after: a person, a team, or an application;
- reaches loopback-bound backends on other machines over hardened SSH tunnels;
- takes a failing backend out of rotation, so a dead node stops swallowing requests. ⚠ It does
  **not** retry a failed request elsewhere: `num_retries` must be **0** here, because on long
  requests it silently multiplies them instead of rescuing them. See
  [Router settings](litellm-gateway-and-api-keys.md#7-router-settings).

Built on [LiteLLM](https://github.com/BerriAI/litellm) (proxy mode), installed fully in userspace
with [uv](https://github.com/astral-sh/uv). No sudo, no containers, no database.

## What it costs in throughput: nothing measurable

At 8 concurrent the gateway adds **no measurable throughput cost**. Above that, unmeasured.
Multi-node routing through a gateway **has been verified by per-backend request counts** — but on
other deployments of ours, not on this one. A sequential burst pinned 11 of 12 requests to one node
under `least-busy` (a *negative* result about that strategy, counted on a different hardware class),
and seven days of production traffic split across two backends to within 0.07% (under
LiteLLM's default weighted-random, not `least-busy`). ⚠ **Neither is a DGX Spark gateway**, and
per-backend counts have not been taken on this one. The method — and why a small burst proves
nothing — is in
[§9 of the operator recipe](litellm-gateway-and-api-keys.md#9-verification-gates); the prefix-cache
cost of spreading traffic is in
[§11](litellm-gateway-and-api-keys.md#11-serving-multiple-nodes-round-robin-scatters-the-prefix-cache).
The figures and the auth gates are in that recipe's header.

## What decides the menu of models

Worth being explicit, because the gateway's model list looks like a menu you can extend at will and
it is not.

**Every `model_name` in the gateway config points at a server that has to already be running.** The
gateway routes requests; it cannot start a model, load weights, or make a model available. Adding a
name to the config for something nothing is serving just produces failing requests. So the menu your
users see is decided by what is running on your nodes, and the gateway only publishes it.

**One machine holds a limited number of models at once, and for large models that number is one.**
These are unified-memory boxes: the weights, the KV cache and everything else share the same pool.
A 4-bit 27B-class model plus its cache occupies a large fraction of a 128 GB node, and a
180B-class model at 4-bit is around **104 GiB** on its own — measured on the GGUF quant in
[the llama.cpp Flash-Next recipe](../dgx-spark/qwen38-flash-next-180b.md). Two of those do not
coexist, whatever the
gateway config says.

⇒ **Multiple model names on one endpoint normally means multiple machines.** One node serving a
fast agentic model and another serving a different model gives users a genuine choice between
two names. Two large models on one node does not.

> [!IMPORTANT]
> **Swapping the large model on a node is a service change, not a config toggle.** Stopping one
> model and starting another takes minutes of load time, and **everyone currently using the old
> name loses it**. Scoping a key controls which of the *currently served* models a person may
> reach; it does not make a model appear for them. Decide what each node serves first, then hand out
> keys against that.

## What it is not

- **Not an inference engine.** It routes; the backends serve. vLLM, SGLang and llama.cpp all sit
  behind it as plain OpenAI-compatible servers.
- **Not a public endpoint.** It binds an address on a private VPN. Clients must be on that VPN
  before a key means anything.
- **Not a multi-tenant identity system.** Keys are static, defined in a small custom auth module.
  Dynamic virtual keys, budgets, revocation and usage tracking need LiteLLM's database mode, which
  is out of scope here.
- **Not high availability.** A single process is a single point of failure. Router settings make it
  survive a dead *backend*, not a dead gateway.

## Where to go next

- **[litellm-gateway-and-api-keys.md](litellm-gateway-and-api-keys.md)** is the operator recipe:
  install, keys, config shape, custom auth, tunnels, router settings, verification gates, and the
  traps that cause outages.
- **[../../connecting-clients.md](../../connecting-clients.md)** is the user-facing handout. Hand this over once the
  gateway is up: joining the network, the three values, and the client settings that matter.

## Two different things are called an API key

Worth separating before you start, because both appear in this repo and they are not interchangeable.

- **A node key** authenticates one inference server. There is one per node, it is created by the
  node's own install, and it is internal: the gateway uses it to talk to that backend. Users
  never see it. Rotating it means writing the file and restarting that node.
- **A consumer key** is what you hand to a person. It lives on the gateway, is scoped to the models
  that person may reach, and is what the rest of this page is about.

Handing out a node key instead of a consumer key would give someone unscoped access to one machine
with no way to revoke them individually. Issue keys at the gateway.

## Standing up a gateway for the first time

**Do this once.** The onboarding checklist below assumes a gateway already exists and is running.
If none does yet, work in this order:

1. **Get a backend serving first.** A gateway routes; it does not run models. Stand up the model you
   want to expose using its recipe, for example
   [Qwen3.8-27B on a DGX Spark with SGLang + NVFP4 + DFlash2](../dgx-spark/qwen38-27b-sglang-nvfp4-dflash2.md),
   and confirm it answers on its own port before going further.
2. **Build the gateway.** Sections 1 to 5 of
   [the operator recipe](litellm-gateway-and-api-keys.md): install into a userspace virtualenv,
   generate the master key and your first consumer key, write `config.yaml` pointing at the
   backend, write `custom_auth.py`, then bind to the private VPN address and launch.
3. **Prove it end to end** with [the verification gates](litellm-gateway-and-api-keys.md#9-verification-gates):
   no key returns 401, a wrong key returns 401, a valid consumer key returns 200, one completion
   comes back, and the same call works **from a different machine** on the VPN.

The gateway can live on the same machine as a backend, reaching it over loopback, or on a separate
node reaching backends through [SSH tunnels](litellm-gateway-and-api-keys.md#6-tunnels-to-backend-nodes).
Same-box is the simpler starting point and is how the DGX Spark build in the recipe's header was
verified. Most of the elapsed time is the package install; the rest is writing a handful of files.

> [!WARNING]
> **The gateway does not survive a reboot.** It and its SSH tunnels are `nohup`, so a restarted
> machine comes back with a dead front door while the node looks healthy. ⚠ **Do not assume the same
> of the backends** — an engine under systemd units, or a container with a restart policy, comes back
> by itself, which is exactly why the node looks fine while nobody can reach it. **Where backends are
> reached through tunnels, a gateway with no tunnels under it fails every request**, so those tunnels
> are as much a part of this as the gateway.
>
> After any reboot: confirm each backend on its **engine** port, not the tunnel port the gateway sees
> it on; re-establish every tunnel and gate each with a direct model-list call; relaunch the gateway
> on the same VPN address; then **re-run the verification gates, including the cross-peer one
> from another machine** — an on-node `curl` passes whether or not the consumer can reach you.
>
> ⚠ **Nobody diagnoses this but you**; a user can recognise it but not act on it. The gates are
> in [the operator recipe](litellm-gateway-and-api-keys.md#9-verification-gates), the durable fix is
> [durable-gateway-user-service.md](durable-gateway-user-service.md), and the full list of what this
> setup does not give you is in
> [Known limits](litellm-gateway-and-api-keys.md#12-known-limits-of-this-recipe).

After that, every additional person is just the checklist below.

## Onboarding a new consumer

The admin-side procedure from "mint a key" to "the user is working", **once a gateway is
already running** (see above if it is not). The commands live in the operator recipe; this is the
order to run them in.

> [!IMPORTANT]
> **Adding or removing a consumer costs a gateway restart, which interrupts everyone.** The gateway
> is a single process in front of all backends, so a restart is a full, brief outage for every
> existing consumer, unlike restarting one backend which costs a fraction of capacity. Batch
> onboarding changes, and do them at a quiet time.

1. **Add their device to the private VPN.** Nothing else works until this is done, and a
   key on its own grants no access.
2. **Generate a key for that consumer** on the gateway node and store it in the permission-restricted
   `.env`. One key per consumer is what keeps a single key revocable without re-keying anyone else
   — though the restart that applies the revocation does briefly interrupt everyone.
   See [Secrets](litellm-gateway-and-api-keys.md#2-secrets).
3. **Add one constant and one `if` branch** for that key in the custom auth module. See
   [`custom_auth.py`](litellm-gateway-and-api-keys.md#4-custom_authpy-master-key-plus-scoped-per-consumer-keys).
4. **Add the models that consumer may reach** to the `models=[...]` allow-list on that branch, using
   names that exist in
   [`config.yaml`](litellm-gateway-and-api-keys.md#3-configyaml-a-model-name-mapped-to-backends).
5. **Restart the gateway**, building the replacement config separately and swapping it in. See
   [Editing the config safely](litellm-gateway-and-api-keys.md#8-editing-the-config-safely).
6. **Run the verification gates**: no header returns 401, a junk key returns 401, the new key returns
   200, a model outside its allow-list is rejected, one chat round-trip returns sensible content, and
   the same call succeeds **from another machine** on the VPN. See
   [Verification gates](litellm-gateway-and-api-keys.md#9-verification-gates).
7. **Hand over `base_url`, the key and the model name out of band.** Never in a chat thread, never in
   a commit.
8. **Substitute the placeholders, then point them at [../../connecting-clients.md](../../connecting-clients.md)** for joining
   the network and the client settings that decide whether their experience is good or confusing.
   connecting-clients.md ships unfilled. Find every placeholder with
   `grep -o '<[A-Za-z_-]*>' connecting-clients.md | sort | uniq -c` — `grep -o` counts occurrences, where `grep -c`
   counts *lines* and under-reports any line carrying two. Take a **copy**, replace the endpoint and admin placeholders with the real
   values, and **leave `<YOUR_API_KEY>` as a placeholder** — the key travels separately, out of band,
   and must never be written into a document you hand around. Verify before sending:

   ```bash
   grep -c '<gateway-host>\|<admin>' <your-copy>.md    # expect 0
   ```

   Hand over the copy, not the repo file.

Steps 3 to 5 are a single edit-and-restart, so batch them if you are onboarding more than one
consumer at a time.

> [!NOTE]
> **"Restart the gateway" means different things on the two deployments**, and every procedure on this
> page ends with one. On a manual `nohup` gateway it is stop-and-relaunch with the same `--host`
> ([§8](litellm-gateway-and-api-keys.md#8-editing-the-config-safely)). **Under
> [durable-gateway-user-service.md](durable-gateway-user-service.md) it is
> `systemctl --user restart <gateway-unit>.service`** — do not `pkill` a unit that carries
> `Restart=always`, or systemd retakes port 4000 before your relaunch does and one of the two
> instances dies.

## Removing a consumer

There is no revocation endpoint in this setup, so removal is the same edit in reverse.

1. Delete that consumer's constant and its `if` branch from the custom auth module, so the key falls
   through to the 401.
2. Delete its line from `.env` on the gateway node.
3. Restart the gateway. The key stops working on the first request after the restart.
4. Confirm with the gates: the removed key must now return **401**.

Removing the device from the VPN is a separate action, and the right one if the person is
losing access entirely rather than losing one model.

## Shared keys, and a key you think has leaked

### Why one key per consumer is not bookkeeping

The keys in this setup are **static strings that carry no identity**. Nothing in a request says who
sent it beyond which constant it matched. So two people sharing one key means:

- **Neither can be cut off independently.** The only revocation available is deleting the constant,
  which removes access for everybody on it.
- **No request can be attributed.** A backend log shows the traffic, not the person, so a runaway
  loop or a surprising prompt cannot be traced back past the key.
- **One person leaving forces a rotation**, and a rotation is a gateway restart plus a re-issue to
  everyone else on that key — an outage for people who did nothing.

Issue a key per consumer at the point where you would want to revoke: a person, a team, or an
application.

### A key you believe is exposed

Order matters. Issue the replacement **first**, so the consumer is never locked out with nothing to
switch to, and **delete** the old line rather than editing it.

1. **Mint the replacement key** and append it to `.env` under a new constant name. See
   [Secrets](litellm-gateway-and-api-keys.md#2-secrets), and append with `>>`.
2. **Delete the old line from `.env` entirely.** Not because editing it in place would fail to revoke
   — the auth module compares the *value* it read at startup, so overwriting the value and restarting
   does revoke the leaked string immediately ([§2](litellm-gateway-and-api-keys.md#2-secrets) rotates a
   key exactly that way). Delete it because a key you believe is exposed should not stay on disk at
   all, and because step 3 points the auth branch at a new constant, leaving the old one dead weight.
3. **Swap the constant in the custom auth module** so the branch reads the new variable, keeping the
   same `models=[...]` allow-list.
4. **Restart the gateway.** Nothing above takes effect until it reloads.
5. **Gate it both ways**: the old key must now return **401**, the new key **200**. One check without
   the other proves half of it.
6. **Hand the new key over by the documented out-of-band method** — not through the channel that
   leaked the old one.

> [!WARNING]
> **If the master key was exposed, re-issue every consumer key in the same pass.** The master key is
> not scoped: it reaches every model behind the gateway with no allow-list at all, so anyone holding
> it had the same access as all of your consumers combined. Rotating it alone tells you nothing about
> what else was in the same place it leaked from.
