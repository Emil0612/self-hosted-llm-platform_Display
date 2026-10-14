# LiteLLM gateway and API keys (no sudo)

```text
Verified against : Strix Halo (gfx1151) nodes as gateway and backends, and a DGX Spark
                   (GB10) node serving Qwen3.8-27B on SGLang behind a gateway on the
                   same box.
Engine / build   : LiteLLM proxy (`litellm[proxy]`) in a uv-managed venv on Python 3.12
Date verified    : 2026-08-31 (Strix Halo build) · 2026-09-07 (DGX Spark build)
                   2026-09-14 (per-person keys, attribution logging, router settings and
                   prefix-cache behaviour behind a two-node gateway)
```

Build steps, auth behaviour, tunnels, router settings and the verification gates have been exercised
on independent gateway builds on both hardware classes.

**The gateway adds no measurable throughput cost at 8 concurrent** (measured on a DGX Spark build,
one run per arm, so it rules out a large cost rather than measuring a small one). Auth gates passed:
no header 401, wrong key 401, scoped consumer key 200, and a completion returned through the full
chain. **Above 8 concurrent, unmeasured.** Multi-node routing through a gateway was exercised on
2026-09-14 — ⚠ though what ran that day was one deployment behind the public name plus a differently-named fallback, so it exercised **dispatch, not the router's multi-node scheduling**. The per-backend counting method, and the counts we do have from other deployments, are in §9; what scattering costs the prefix cache is in §11.

Upstream tools: [LiteLLM](https://github.com/BerriAI/litellm), [uv](https://github.com/astral-sh/uv),
OpenSSH, FastAPI (via LiteLLM).

> [!NOTE]
> **The port numbers below (gateway 4000, local backends 8000-8001) are illustrative.** They are
> written out concretely so the commands are copy-pasteable; `<tunnel-port>` is whichever free local
> port you forward a remote backend to. Any free port works; substitute your own consistently.

---

## 1. Install, fully userspace

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # static binary to ~/.local/bin, no sudo
mkdir -p ~/llm-gateway && cd ~/llm-gateway
uv venv --python 3.12                              # pin 3.12; newer system Pythons hit wheel gaps
source .venv/bin/activate
uv pip install "litellm[proxy]"
```

> [!WARNING]
> **The `[proxy]` extra is required.** `pip install litellm` gives a library that *imports perfectly*
> and then dies at runtime: the first launch exits 1 with `ModuleNotFoundError: No module named
> 'websockets'`. **Verify by RUNNING the server, not by importing the package.**

## 2. Secrets

Keys are generated **on the node**, stored with restrictive permissions, referenced from the
environment. Never pasted into chat, never committed, never echoed to a transcript.

Pick a short identifier per consumer of the gateway (a person, a team, or an application) and
substitute it for `<NAME>` and `<name>` below. **One key per consumer is what makes a single key
revocable without re-keying anyone else**, so choose the granularity you will actually want to
revoke at.

**First time only — this creates the file and will overwrite anything already there:**

```bash
cd ~/llm-gateway
umask 077
{ echo "LITELLM_MASTER_KEY=sk-$(openssl rand -hex 24)"
  echo "<NAME>_KEY=sk-<name>-$(openssl rand -hex 24)"; } > .env
chmod 600 .env
ls -l .env      # expect -rw------- ; do not cat this file
grep -c '_KEY=' .env    # expect 2
```

**Every consumer after the first — append, never re-create:**

```bash
cd ~/llm-gateway
cp .env .env.bak-$(date +%Y%m%d)                              # cheap insurance, see the warning
umask 077
echo "<NAME>_KEY=sk-<name>-$(openssl rand -hex 24)" >> .env    # NOTE: >>
grep -c '_KEY=' .env    # count only, never print the file: expect one more than before
```

> [!WARNING]
> ★ **`>` truncates, `>>` appends, and getting this wrong destroys every key on the gateway.** Re-running
> the create block, or writing a new consumer line with `>`, empties `.env` of the master key and of
> every key you have ever issued. **They cannot be re-derived**: the values are random and are stored
> nowhere else, so every consumer has to be re-keyed and re-contacted.
>
> **The damage is delayed.** The running gateway holds the old values in its own environment, so it
> keeps serving and every test passes; the failure surfaces at the **next restart**, which is the very
> next step of onboarding.
>
> Take `cp .env .env.bak-$(date +%Y%m%d)` before touching the file, and check the line count with
> `grep -c '_KEY=' .env` after every edit.

⚠ **Do not give the master key a self-describing prefix.** A consumer key benefits from one — it
aids attribution and tells you which key you are holding — but a string beginning `sk-master-`
announces itself as the unscoped credential to anyone who finds it. Keep it opaque.

Add `.env` to `.gitignore` before the directory ever becomes a repo. Rotation means writing the new
value into the file and restarting the gateway.

### Handing the key over out of band

The key must never be printed to a terminal, a chat thread or a transcript, but it does have to reach
one person. Extract **only the key being issued** into its own file, move that file by whatever
out-of-band channel the team already uses, and delete it:

```bash
cd ~/llm-gateway
umask 077
grep '^<NAME>_KEY=' .env | cut -d= -f2- > ~/<name>.key   # this one key only, nothing else
chmod 600 ~/<name>.key
wc -c ~/<name>.key      # verify by LENGTH, never by printing: expect 48 hex characters, plus
                        # your sk-<name>- prefix, plus one for the trailing newline
```

Move `~/<name>.key` out of band — a shared password manager, a one-time-secret link, or `scp`
straight to the recipient's machine — then destroy the copy on the node and confirm it is gone:

```bash
shred -u ~/<name>.key 2>/dev/null || rm -f ~/<name>.key
ls ~/<name>.key         # expect: No such file or directory
```

## 3. `config.yaml`: a model name mapped to backends

Same-named entries form one **model group**; LiteLLM load-balances across them.

```yaml
model_list:
  - model_name: <public-model-name>
    litellm_params:
      model: openai/<public-model-name>
      api_base: http://127.0.0.1:8000/v1     # backend local to the gateway node
      api_key: dummy                          # correct ONLY for a backend launched with no key
                                              # of its own; see below if yours is key-authed
  - model_name: <public-model-name>
    litellm_params:
      model: openai/<public-model-name>
      api_base: http://127.0.0.1:<tunnel-port>/v1   # a second node, via an SSH tunnel

  - model_name: <embedding-model-name>
    litellm_params:
      model: openai/<embedding-model-name>
      api_base: http://127.0.0.1:8001/v1

general_settings:
  custom_auth: custom_auth.user_api_key_auth
```

**If the backend is key-authed, reference the key from the environment and never inline it.** Not
every backend is keyless: the Qwen3.8-27B DGX Spark build this recipe sends you to stand up serves on
**port 30000 and requires a key**, so `api_key: dummy` against it returns 401 on every request.

```yaml
  - model_name: <public-model-name>
    litellm_params:
      model: openai/<public-model-name>
      api_base: http://127.0.0.1:30000/v1          # the key-authed DGX Spark backend
      api_key: os.environ/QWEN38_BACKEND_KEY       # resolved at load time; the value never
                                                   # appears in this file
```

`os.environ/<VAR>` is LiteLLM's own indirection: the variable is read when the config is loaded, so
the secret stays out of the file and out of any copy of it. Append the variable to `.env` the same way
as a consumer key, with `>>` and a count check — and **read the value from the file it already lives
in rather than typing it**. The backend node's install wrote it to `~/.config/qwen38/api-key`, mode
`600` (see
[qwen38-27b-sglang-nvfp4-dflash2.md](../dgx-spark/qwen38-27b-sglang-nvfp4-dflash2.md#4-the-api-key)).

```bash
cd ~/llm-gateway
umask 077

# gateway on the SAME box as the backend: read the key file directly
{ printf 'QWEN38_BACKEND_KEY='; cat ~/.config/qwen38/api-key; echo; } >> .env    # NOTE: >>

# gateway on a DIFFERENT box: read it over ssh, still never through the terminal
{ printf 'QWEN38_BACKEND_KEY='; ssh <user>@<backend-node> 'cat ~/.config/qwen38/api-key'; echo; } >> .env   # NOTE: >>

grep -c '=' .env    # count only: expect one more than before
```

> [!IMPORTANT]
> **Neither form prints the key.** Typing it on a command line would put it in your shell history and
> in the transcript, which is exactly what section 2's rule forbids; every other key in this recipe is
> generated inline by `openssl rand` for the same reason. The `echo` after the `cat` is there because
> the backend writes its key file **without a trailing newline**, and a glued `.env` line breaks the
> next variable.

New backends must be **inserted before `general_settings:`**, not appended. Keep backends behind one model name
**homogeneous** (same model, quant and serving flags), or latency and response shape vary by which
machine answered.

## 4. `custom_auth.py`: master key plus scoped per-consumer keys

LiteLLM needs a database only for *dynamic* virtual keys. For a handful of static keys this keeps
the stack database-free.

> [!IMPORTANT]
> **`config.yaml` and `custom_auth.py` both live in `~/llm-gateway`, alongside `.env`.** That is not
> cosmetic: `custom_auth: custom_auth.user_api_key_auth` is a **Python import**, resolved from the
> process's **working directory**, not from the config file's location. The module has to sit in the
> directory you launch from. Launch from anywhere else and the proxy dies at startup with
> `ModuleNotFoundError: No module named 'custom_auth'` — which is why every launch and restart in this
> recipe begins with `cd ~/llm-gateway`.

```python
import os
from fastapi import Request, HTTPException
from litellm.proxy._types import UserAPIKeyAuth

MASTER   = os.environ["LITELLM_MASTER_KEY"]
CONSUMER = os.environ["<NAME>_KEY"]    # one constant per consumer key you issued in step 2

async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    k = (api_key or "").replace("Bearer ", "").strip()   # no header means api_key arrives as None
    if k == MASTER:
        return UserAPIKeyAuth(api_key=k)                  # all models
    if k == CONSUMER:
        return UserAPIKeyAuth(api_key=k,
                              models=["<public-model-name>", "<embedding-model-name>"])
    raise HTTPException(status_code=401, detail="Invalid API key")
```

> [!WARNING]
> **Two auth failure modes to test for. Both are one line.**
> 1. A bad key must `raise HTTPException(status_code=401, ...)`, never a plain `raise Exception`. A
>    plain exception surfaces as **HTTP 500**, which reads as "the platform is broken" instead of "your
>    key is wrong" and sends people down the wrong path.
> 2. A request with **no `Authorization` header at all** makes `api_key` arrive as `None`, and
>    calling a string method on it raises `AttributeError`, giving **500 instead of 401**. A *wrong*
>    key was always handled correctly; only a *missing* one broke. **Fix: coerce to empty string,
>    `(api_key or "")`.**
>
> Test both cases. They are different code paths.

Keep the allow-list in step with `config.yaml`, and add one constant plus one `if` branch per
additional consumer key.

### One key per person

Issue a **separate key per person** rather than one shared team key: one constant and one branch
each, and you can revoke one person without re-keying everyone. **Label them generically** — `user-a`,
`user-b` — so names stay out of the config and the logs.
⚠ **Keep a label→person map somewhere safe**, or distinct keys buy nothing.

### Attribution: the proxy will not log it for you

> [!WARNING]
> **Returning `UserAPIKeyAuth(user_id=…)` does not get the caller into the log.** At default
> verbosity a real request logs **nothing** identifying the key that made it.

The obvious fix is the wrong one. **Turning on debug request logging writes the outgoing
`Authorization` header to disk** (§10) — a worse problem than the one you were solving. And note that
this is separate from a risk you carry anyway: **the master key can appear in the proxy's startup
banner at any verbosity** (§9), so a log is worth treating as sensitive whether or not debug logging
is on.

**Log it yourself, at the moment you authenticate.** `custom_auth.py` already runs on every request
and already knows the label, so it is the cheapest correct place:

```python
import datetime

def _note(label: str) -> None:
    try:
        with open("access.log", "a") as fh:
            fh.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {label}\n")
    except Exception:
        pass          # a logging failure must never break authentication
```

Call it on each successful branch. No database, no debug mode, no secret written anywhere: a
timestamp and a label, which is exactly what attribution needs and nothing more.

> [!IMPORTANT]
> **The `try/except` is the load-bearing part.** This code runs inside the auth path. A full disk or
> a permissions change must degrade to "we lost some attribution", never to "nobody can authenticate".
> Test it by making the log path unwritable and confirming requests still succeed.

⚠ This records who called and when — **not** whether their call succeeded, how many tokens it used,
or how long it ran. Per-key usage and budgets need LiteLLM's database mode. See also the detection
limit in §12.

## 5. Binding and launch

Bind the **private VPN address**. Not `0.0.0.0`, which exposes the LAN, and not `127.0.0.1`,
which is reachable only from the gateway node itself. `<vpn-address-of-this-node>` below is this
node's address on the private VPN, and the consumer's machine must be on the same VPN for that
address to mean anything.

```bash
cd ~/llm-gateway && source .venv/bin/activate
set -a && source .env && set +a        # exports the keys so custom_auth can read them
HOST=<vpn-address-of-this-node>
nohup litellm --config config.yaml --host "$HOST" --port 4000 \
  > ~/llm-gateway/litellm.log 2>&1 &
echo ${#HOST}                          # sanity-check the capture by LENGTH, never by printing it
```

> [!WARNING]
> **A process command line is NOT authoritative for the bind address.** The proxy runs as more than
> one process, and a worker's command line can carry a loopback `--host`. Recovering it with
> `pgrep -af litellm | grep -o -- "--host [0-9.]*" | head -1` can return `127.0.0.1` on a gateway that
> is correctly bound to the VPN address.
> Relaunching on that leaves the gateway running, the port listening and every on-node `curl`
> passing, while the consumer's endpoint is dead. **Read the listening socket instead:**
> ```bash
> BIND=$(ss -tln | grep ":4000 " | awk '{print $4}' | sed 's/:4000$//' | head -1)
> ```

`curl 127.0.0.1:4000` returning nothing (code 000) is expected, not a fault; use `$BIND:4000`. Keep
the log path stable: a path that drifts between restarts leaves later debugging tailing a retired
file.

## 6. Tunnels to backend nodes

Backends stay bound to `127.0.0.1` on their own machines. The gateway reaches them over SSH forwards,
so no inference server is ever exposed on the network.

```bash
ssh-keygen -t ed25519 -N ""       # ONLY if this node has no key. Never regenerate an existing one;
                                  # it is already authenticating other tunnels
ssh-copy-id <user>@<backend-node>

nohup ssh -N -L <tunnel-port>:localhost:8000 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
  <user>@<backend-node> &
```

`ExitOnForwardFailure=yes` makes a tunnel that cannot bind its port die loudly rather than sit there
as a healthy-looking session forwarding nothing.

> [!IMPORTANT]
> **Gate each tunnel with a direct model-list call BEFORE the gateway knows it exists:**
> `curl -s --max-time 10 http://127.0.0.1:<tunnel-port>/v1/models`. This proves SSH auth, the forward and
> backend liveness with zero blast radius. Only then add the entry to `config.yaml`.
>
> ⚠ **Against a key-authed backend that call returns 401, not a model list.** Send the key from a
> mode-600 curl config — after `set -a && source .env && set +a`:
>
> ```bash
> umask 077
> printf 'header = "Authorization: Bearer %s"\n' "$QWEN38_BACKEND_KEY" > ~/.curlauth
> curl -s --max-time 10 -K ~/.curlauth http://127.0.0.1:<tunnel-port>/v1/models
> rm -f ~/.curlauth
> ```
>
> ⚠ **Taking the key from the environment is not by itself enough.** `-H "Bearer $VAR"` is expanded by
> the shell before `curl` runs, so the value lands in the process table exactly as if it had been
> typed. `printf` is a builtin: it forks nothing and creates no argument.
>
> A 401 still proves SSH auth and the forward reached a live server; only the model list also proves
> the backend is serving what you think it is.

`ss -tln | grep -c ":<tunnel-port> "` returns twice what you expect, because `ssh -L` binds IPv4 *and* IPv6.
Not a fault. Nothing here restarts a tunnel that dies, and router settings will not resurrect the
forward either — at best they take a failing deployment out of rotation (§7), which is narrower than
"routing around" it.

## 7. Router settings

```yaml
router_settings:
  cooldown_time: 30      # seconds a failing deployment is out of rotation
  allowed_fails: 2       # failures before that cooldown applies
  num_retries: 0         # see the warning below. Do not raise this for long-running requests
```

Without the first two a dead backend **stays in rotation**: with two nodes behind one model name,
expect a burst of failures as requests land on the dead one, after which service continues on the
survivor at half capacity. ⚠ **We have never observed this in the wild**, so treat the failure burst as the expected shape
rather than something we measured. What these settings close is **detection**: without them nothing
tells you a backend is gone. Config-only, so bundle them into a restart you already need.

> [!WARNING]
> **`num_retries` above 0 silently multiplies long requests. Set it to 0.**
>
> A request that runs long enough to trip the proxy's internal timeout is **re-sent**. Nothing tells
> the caller. With a router underneath, each attempt is routed afresh, so **one client request became
> up to three full generations on different nodes, and none of them ever returned to the client.**
>
> The symptoms it produces look like anything except a retry setting, which is why it took an evening
> to find:
> - Requests that never complete, while the backends look perfectly healthy.
> - Work that appears to **migrate between machines** mid-request.
> - **Wildly inconsistent throughput readings**, because you are watching one of several generations
>   for the same request.
>
> Setting it to **0** fixed it: the very next run completed cleanly on one node. ⚠ **One run after
> the change, and we did not re-test with retries back on** — the mechanism is well understood and
> the config is cheap, so we did not spend a reproduction on it.
>
> ⚠ **This is the same failure we already document for clients.** [connecting-clients.md](../../connecting-clients.md#set-max_retries0-and-a-client-timeout-of-at-least-1800-s)
> tells users to set `max_retries=0` for exactly this reason — retries on a slow-but-working
> request make things worse, not better. **Any hop
> that can retry needs this, not just the outermost one.**
>
> `fallbacks` is a **separate mechanism** and is unaffected — see below. Turning retries off does not
> cost you failover.

### Primary and fallback: two model names, not two backends under one

A common reason to want a second entry is "use this backend, and that one if the first is gone".
**That is not what two same-named entries do.**

> [!WARNING]
> **Entries sharing a `model_name` load-balance; they do not fail over.** If you list a primary and a
> standby under one name, roughly half your traffic goes to the standby all the time. When the
> primary is the only path that has some property you need — a router every request should pass
> through, or a cache-warm node — half of everything quietly bypasses it.

Give them **different** `model_name`s and declare the relationship explicitly:

```yaml
router_settings:
  fallbacks: [{"<public-model-name>": ["<fallback-model-name>"]}]
```

Callers only ever use `<public-model-name>`; the fallback name exists for the router.

⚠ **Two things that will bite you here.** First, if you scope keys with `custom_auth.py` (§4), the
**allow-list must include the fallback name too**, or your own scoping blocks the failover at the
moment it is needed. Second, and more important:

> [!WARNING]
> **Fallbacks protect against a backend *stopping*. They do nothing about one that is *degrading*.**
> A backend returning correct `200`s several times slower than normal is not a failure by any
> definition the router has, so nothing fails over. And you cannot close the gap with a timeout: on a
> long-context stack a legitimate request genuinely takes 60-170 s, so any timeout short enough to
> catch degradation also kills real work. **Degradation is caught by watching, not automatically.**
> Decide in advance who looks.

**Routing strategy.** The default is weighted-random shuffle. `least-busy` picks the deployment with
fewest requests *in flight*, which is the right shape under concurrent load. ⚠ We have not measured this under
concurrent load in a way that distinguishes it from weighted-random. The sequential counter-case
next is the one we did establish. Under **sequential** load, though, every deployment is at zero, every choice is a
tie, and it pins traffic to one node (measured 11/1 at 12 sequential). Choose against the traffic
shape you actually have. `least-busy` is in-memory in a single proxy process; Redis matters only for
multiple proxy instances. It also structurally favours whichever backend drains fastest, so keep the
backends behind one model name homogeneous.

## 8. Editing the config safely

The running service must never be in a broken state: build the replacement separately, read it, then
move it into place.

```bash
cd ~/llm-gateway
cp config.yaml config.yaml.bak-$(date +%Y%m%d)
echo "" >> new.yaml          # FIRST: see the warning below
# ...build the replacement in new.yaml...
cat new.yaml                 # read it in full before it goes near the service
mv new.yaml config.yaml      # atomic swap
```

> [!WARNING]
> **Append a blank line before appending a block.** A config file whose last line has **no trailing
> newline** gets the next appended line **glued onto it**:
> `custom_auth: custom_auth.user_api_key_auth router_settings:`. The proxy then refuses to start, or
> starts with auth broken. `echo "" >> file` is safe either way: it completes a dangling line, or
> adds one harmless blank one.

To insert entries before `general_settings:` rather than appending:

```bash
G=$(grep -n "^general_settings:" config.yaml | cut -d: -f1)
head -n $((G-1)) config.yaml >  new.yaml
cat new_backends.yaml        >> new.yaml
tail -n +$G      config.yaml >> new.yaml
```

Restarting means capturing the bind address from `ss`, stopping the process, and relaunching with
the same `--host`. ⚠ **Under the durable recipe, do not do that.** If this gateway runs as a systemd
unit ([durable-gateway-user-service.md](durable-gateway-user-service.md)), the unit carries
`Restart=always` with `RestartSec=30` and `StartLimitIntervalSec=0`. Kill the process and systemd
brings the unit back 30 seconds later — usually *after* your manual relaunch has taken port 4000 — so
the unit then fails to bind and, because `StartLimitIntervalSec=0` means systemd never gives up,
retries a port it can never get, forever. You are left with a manual gateway that vanishes at the next
reboot and a unit in a permanent restart loop. Restart it with
`systemctl --user restart <gateway-unit>.service` instead; the `--host` lives in the unit file. ⚠ A gateway restart is 100% of traffic where restarting one backend is `1/N`, so batch changes
rather than restarting per consumer.

## 9. Verification gates

| gate | method | expected |
|---|---|---|
| Backend answers directly | `curl --max-time 10 http://127.0.0.1:800X/v1/models`; for a key-authed backend add `-H "Authorization: Bearer $QWEN38_BACKEND_KEY"` after sourcing `.env` | model list, per backend. **Without the header a key-authed backend returns 401**, which proves it is listening and nothing more |
| **Unauthenticated request** | `curl -s -o /dev/null -w '%{http_code}' http://$BIND:4000/v1/models` | **401** (not 500, not 200) |
| **Wrong key** | same, junk bearer token | **401** |
| **Consumer key** | same, with the key supplied via `-K` (see below) | **200** |
| Model scoping | consumer key against a model not in its allow-list | rejected |
| **Model-list scoping** | `/v1/models` with a consumer key whose allow-list excludes a served model, counting occurrences of that model name: `curl -s -H "Authorization: Bearer $<NAME>_KEY" http://$BIND:4000/v1/models \| grep -c '<excluded-model-name>'` | **0** = the list is filtered per key · **1** = it is not |
| Chat round-trip | one completion through the gateway, consumer key | sensible content |
| **Every issued key, individually** | one call per key, starting with the **least-used** one | **200** each |
| **No secret in the log** | count, never read — see below | **0** |
| **Cross-peer reachability** | the same call **from a different machine** on the VPN | 401 or 200, not a timeout |
| **Routing** | count real requests per backend (below) | every backend serving |

> [!IMPORTANT]
> **The `-H "Authorization: Bearer $KEY"` form above is written for readability, and it leaks.** The
> shell expands the variable before `curl` runs, so the live key sits in the process table for the
> life of the request. Put the header in a mode-600 file and read it with `-K`:
>
> ```bash
> umask 077
> printf 'header = "Authorization: Bearer %s"\n' "$<NAME>_KEY" > ~/.curlauth
> curl -s -o /dev/null -w '%{http_code}\n' -K ~/.curlauth http://$BIND:4000/v1/models
> rm -f ~/.curlauth
> ```
>
> `printf` is a shell builtin, so it forks no process and creates no argument to read.

> ⚠ `umask 077` **before** the redirect, not after — the file is created by the redirect, so a later
> `umask` cannot help. Keep it out of `/tmp`, which is world-readable by default.

**Test every key, and test the least-used one first.** A typo in one environment variable name breaks
exactly one person, and it will be the person who calls least — so it surfaces days later as "the
platform is broken for me", long after you have stopped associating it with the build. Walking all of
them costs one `curl` each.

**Check the log for secrets by counting, not by reading.** Reading a log to see whether it contains
your keys is a good way to put them on a terminal that is being shared or recorded. Pass the patterns
in from a file so they never become command-line arguments either:

```bash
umask 077                                          # before the file exists, not after
cd ~/llm-gateway
cut -d= -f2- .env > .keycheck                      # the secret VALUES, nothing else
grep -cFf .keycheck litellm.log                    # expect 0
shred -u .keycheck 2>/dev/null || rm -f .keycheck
```

> [!NOTE]
> **Under systemd there is no `litellm.log`.** The unit in
> [durable-gateway-user-service.md](durable-gateway-user-service.md) sets no `StandardOutput=`, so the
> proxy's stdout — **startup banner included** — goes to the systemd user journal and this gate fails
> with "No such file or directory" rather than returning 0. Scan the journal instead:
> `journalctl --user -u <gateway-unit> --no-pager | grep -cFf .keycheck` (still `expect 0`), and treat
> the journal with the same care as the file: count, never `tail` it on a shared screen.

> [!WARNING]
> **Never blind-`tail` a gateway log.** LiteLLM can print the master key in its **startup banner**,
> so the top of a fresh log is one of the likelier places for a secret to be sitting. The count above
> is safe; `tail -f` on a shared screen is not.

**Reachability must be tested from another machine, and it should be the last gate you run.** Every
on-node test passes trivially and proves nothing about the consumer's experience. **An external peer
reaching the VPN-bound port is the one hop that no on-box check exercises**, and a failure there
is indistinguishable from "the platform is broken" when the first user tries it. **Make it the final
gate of any gateway build.**

**Run the model-list scoping gate once per gateway build and write the answer down.** We have not
measured whether this LiteLLM version filters `/v1/models` per key, and it is not safe to assume
either way, because [connecting-clients.md](../../connecting-clients.md) tells users to discover the served models with
exactly that endpoint. If the count comes back 1, the list is global: a consumer sees names their key
cannot call, and you should tell them which ones are theirs rather than let them find out with a
rejected request. Do not assert an answer we have not measured on the build in front of you.

**Routing is verified by counting real requests in each backend's own log**, never by a response
field. `system_fingerprint` is *model*-derived and therefore identical across replicas serving the
same model, so it cannot tell you which backend answered and cannot verify routing.

```bash
grep -c "POST /v1/chat/completions" ~/<backend>.log   # on each backend, before and after a burst
```

A 24-request burst returning 4 / 7 / 7 / 6 across four backends, summing to exactly 24, is proof.
Two requests are not: default routing is weighted random, so a small sample is a coin flip. Run the
burst with a **consumer** key so it validates model scoping too, and give every `curl` a
`--max-time` so a hung request is distinguishable from a slow one. With live traffic you need no
burst at all, because comparing cumulative POST counts is stronger and adds zero load (seven days of
real traffic across two backends split to within 0.07%). Some backends log no HTTP access lines at
all, and an embedding server may log slot launches instead, so find the line the engine actually
emits before concluding a backend is serving nothing.

## 10. Reasoning effort through the gateway

**The gateway's only job here is to pass the parameter through unaltered.** What lower reasoning
effort is *worth* is a property of the model behind it, varies by an order of magnitude between
workloads, and does **not** transfer to the 180B long-context model — see
[numbers.md §4](../../reference/numbers.md#4-reasoning-effort-an-agent-loop-lever-not-a-per-request-one).

**Two findings about forwarding, measured on different stacks — do not assume either carries across.**

> **`chat_template_kwargs` IS forwarded to an OpenAI-compatible backend unmodified**, with no
> `allowed_openai_params` entry needed. Measured on a DGX Spark (GB10) backend.
>
> **A bare `reasoning_effort` parameter IS blocked** (`UnsupportedParamsError`) and needs
> `allowed_openai_params: ["reasoning_effort"]` in that model's `litellm_params`.
> — measured on a **different hardware class and a different model**: a LiteLLM gateway in front of
> gpt-oss on Strix Halo (gfx1151) nodes. **Not measured on the DGX
> Spark.**

> [!WARNING]
> **The two behave differently, so do not assume one from the other** — and they were measured on
> different stacks, so do not assume either one carries across. Do **not** reach for
> `drop_params: true`: it silently discards the parameter, which is strictly worse than a visible
> 400.

**Diagnostic: does the proxy actually forward parameter X?**

```bash
# 0. the log will contain live credentials: private directory, private permissions, BEFORE it exists
umask 077
cd ~/llm-gateway

# 1. relaunch with detailed request logging on a THROWAWAY port
litellm --config config.yaml --host 127.0.0.1 --port 4001 --detailed_debug > dbg.log 2>&1 &
# 2. send exactly ONE request carrying the parameter
# 3. COUNT occurrences in the payload actually sent upstream (prints a number, never a line)
awk '/Request Sent from LiteLLM/{f=1} f&&/X/{c++} END{print "FWD="c+0}' dbg.log

# 4. STOP the debug proxy FIRST — shredding a file it still has open frees no secret:
#    the process keeps writing plaintext headers to the unlinked inode via its own fd
pkill -f "litellm --config config.yaml --host 127.0.0.1 --port 4001"
pgrep -af "port 4001"                      # expect no output BEFORE the next line

# 5. then destroy the log, in the same sitting, not at teardown
shred -u dbg.log 2>/dev/null || rm -f dbg.log
```

> [!WARNING]
> **A detailed-debug log contains the outgoing `Authorization` header in plaintext.** Never `cat`,
> `tail`, `less` or plain-`grep` it; use only counting commands (`grep -c`, the `awk` above). **Keep
> it out of `/tmp`**, which is world-readable by default and shared with every other user on the box,
> and **destroy it as part of this procedure** rather than leaving it for teardown — a debug log that
> outlives the question it answered is a credential sitting on disk for no reason.

## 11. Serving multiple nodes: round-robin scatters the prefix cache

> [!IMPORTANT]
> **Two nodes behind one model name with plain round-robin scatter a multi-turn conversation across
> machines.** Each turn lands wherever the router points, so a turn can arrive at a node that has
> never seen that conversation and must prefill it from scratch. A warm prefix cache is worth roughly
> an order of magnitude on time-to-first-token, so this is not a small effect — the measurement is in
> [numbers.md §6](../../reference/numbers.md#asking-a-second-question-about-the-same-document-is-10x-cheaper).

Two ways to keep it:

1. **Give each node its own `model_name`** so a client that picks one stays on it for the whole
   conversation. Simple, no new component, but it pushes the choice onto the caller.
2. **Put a cache-aware router in front of them**, routing by prefix affinity rather than by load or
   at random. The right answer for a pool that must look like one endpoint.

Plain round-robin stays correct for **stateless, single-shot** traffic (embeddings, classification,
one-question calls), where there is no conversational prefix to preserve.

### ★ How much scattering actually costs is bounded

**On the reading that fits the measurement** — the cache is per node and persists — scattering does not cost a cold prefill every turn: it
costs at most **one extra cold prefill per node**. With two nodes you pay it twice instead of once,
then every later turn is warm wherever it lands. ⚠ That bound scales with node count, and cache
blocks are evicted under memory pressure, so "warm" decays on a busy box. The measurement, and what
it does and does not establish, is in
[numbers.md §6](../../reference/numbers.md#asking-a-second-question-about-the-same-document-is-10x-cheaper).

> [!WARNING]
> **Deployment affinity cannot help if all your traffic points at one deployment.** LiteLLM can pin a
> caller to a deployment (by session, by key), which is the right tool — but it pins a *deployment*,
> and if a single router entry fronts your whole pool there is nothing to pin between. Getting
> per-node affinity then means the gateway addressing the nodes directly, which removes whatever sat
> in between. **Check which one you have before assuming affinity is a setting you can switch on.**
>
> ★ The symptom to listen for from users: *"the first question is slow, and so is every one after
> it."* That is a cache that is never being reused. **"Slow first, fast after" is the healthy
> shape** — see §6 of the numbers page.

## 12. Known limits of this recipe

- **Nothing in *this* recipe is reboot-persistent.** The gateway and its tunnels are `nohup`, so a
  reboot leaves the front door dead. ⚠ **The backends are a separate question and several do come
  back on their own** — the containerised long-context stack restarts under a Docker restart policy,
  and an engine installed with systemd units returns with the machine. A healthy-looking node behind
  a dead gateway is the normal shape of this outage. A per-node start script is the low-commitment substitute; user systemd
  units are the durable option, at the cost of state that keeps restarting until it is removed. The
  recipe for those is [durable-gateway-user-service.md](durable-gateway-user-service.md).
- **Static keys only.** No budgets and no per-key usage tracking; those need LiteLLM's database mode.
  The `access.log` in §4 gives you who and when, and nothing else.
- ⚠ **Revoking one key interrupts everyone.** Revocation here means editing `.env` and
  `custom_auth.py` and **restarting the gateway** — which §8 prices at 100% of traffic, because the
  gateway is the single entry point. So per-person keys do let you cut off one person without
  re-keying anybody else, but the phrase "without disturbing anyone else" is wrong about the restart.
  There is no runtime revocation without database mode. Batch key changes rather than doing them
  one at a time.
- **Single gateway process** means a single point of failure.
- **No log rotation** anywhere in this stack. Watch a high-volume backend's log size.
- ⚠ **An abandoned request keeps generating.** A client disconnecting does **not** propagate through
  the gateway to the engine: the backend carries on producing tokens into a connection nobody is
  reading, holding a concurrency slot the whole time. Restarting the gateway closes the upstream
  connection and the engine then aborts the work, which is the recovery, but it is a blunt one.
  **A working `max_tokens` ceiling is what bounds this** — with one, the worst case is a few wasted
  minutes of a slot; without one it is unbounded.
- ⚠ **You cannot see a key-holder's failures from here.** If their client gives up at 60 s while the
  backend generates for 160 s and returns a `200`, your logs record a success and theirs records a
  failure — and both are right. Healthy backends, perfect routing and zero errors on your side are
  **fully consistent** with a consumer seeing a high failure rate. When someone reports trouble, ask
  for `finish_reason`, `completion_tokens`, their client timeout and their retry setting; do not try
  to find their failures in your own logs, because they are structurally not there.

## 13. Teardown

Removing the gateway again, in order. Nothing here needs sudo.

> [!WARNING]
> **Step 4 destroys every key on this gateway, and they cannot be re-derived.** `.env` holds the
> master key and every consumer key, the values are random, and they are stored nowhere else — so the
> `.env.bak-*` copies go with it too, in the same `rm -rf`. **If you are rebuilding rather than
> retiring, move `.env` somewhere safe before you start**, and remember every consumer has to be
> re-keyed and re-contacted if you do not.

> [!NOTE]
> **Steps 1 and 2 do not stick if this gateway is under systemd.** If you followed
> [durable-gateway-user-service.md](durable-gateway-user-service.md), the gateway unit and the tunnel
> units all carry `Restart=always`, so each `pkill` below is followed immediately by a restart and
> neither `expect no output` nor `expect 0` ever passes. Run
> `systemctl --user disable --now <gateway-unit> <tunnel-unit>` first — disabling, not just stopping,
> so a reboot part-way through teardown does not bring them back.
>
> **Check the tunnel's own port and nothing wider.** If a backend runs on this same node it is
> listening on `:8000`/`:8001` by design, so a gate written `grep -c ":800"` can never return 0 —
> and chasing that to zero means killing a live backend. Substitute whichever port you tunnelled.

```bash
pkill -f "litellm --config config.yaml"    # 1. the gateway process
pgrep -af litellm                          # expect no output

pkill -f "ssh -N -L <tunnel-port>:"        # 2. the backend tunnel from section 6 (one per port)
ss -tln | grep -c ":<tunnel-port> "        # expect 0 (name the tunnel's own port, not ":800")

rm -f ~/llm-gateway/dbg.log               # 3. the detailed-debug log, if section 10 was run
                                           #    (it contains an Authorization header)
rm -rf ~/llm-gateway                      # 4. venv, config.yaml, custom_auth.py, .env, backups
```

**Nothing was installed on the host**: `uv` is a static binary under `~/.local/bin` and the whole
stack lives in `~/llm-gateway`. **Backends are untouched** — they keep serving on their own ports,
which is the point of the tunnels. If you also want the backends down, that is their own runbook's
teardown, not this one.




---

**See also.** [runbooks/gateway/README.md](README.md) for the onboarding flow this recipe
supports · [durable-gateway-user-service.md](durable-gateway-user-service.md) to make the gateway
survive a reboot · [../../connecting-clients.md](../../connecting-clients.md) for what a key-holder receives.
