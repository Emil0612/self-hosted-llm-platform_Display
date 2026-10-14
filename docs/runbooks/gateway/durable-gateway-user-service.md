# Making the gateway survive a reboot: user systemd units (no sudo)

```text
Verified against : one node of one hardware class, single-node rehearsal. Nothing here is
                   GPU- or vendor-specific (user systemd only), but it has been rehearsed on one
                   machine, not ported.
Engine / build   : systemd user units + `loginctl enable-linger`, plus a real-completion
                   liveness probe on a timer. Rehearsed against a stand-in engine.
Date verified    : 2026-07-14 (rehearsal, all three break-tests passed); the
                   maintenance-window stop race in this page was found live 2026-08-24
```

The [operator recipe](litellm-gateway-and-api-keys.md) runs everything under `nohup`, which does not
survive a reboot. This is the durable version of the same stack.

**What it buys you:**

- The engine **comes back automatically after a reboot**, before anyone logs in — that is the part
  the rehearsal proved. The tunnel and gateway units follow the same pattern and are ordered ahead of
  the gateway with `After=`/`Wants=`, but **that ordering is derived rather than measured**; see
  section 11.
- A **wedged engine gets restarted without a human**, because a probe makes a real completion on a
  timer rather than trusting a health endpoint.

Without it, a rebooted node comes back **looking healthy with a dead front door**: the machine is up,
you can SSH to it, and every consumer call times out. Nobody outside the node can diagnose that —
[connecting-clients.md](../../connecting-clients.md) correctly sends a user to check their own VPN connection first.

Upstream tools: systemd (user instance), `loginctl`, `curl`. Nothing is installed and nothing needs
sudo.

> [!NOTE]
> Every `<angle-bracket>` below is yours to substitute: unit names, ports, model names, addresses and
> the backend user/host. The unit bodies are shown as **the shape to write**, not as files we ship.

---

## 1. Enable lingering, first

User units stop when your last session ends, and do not start at boot. Lingering is what makes a user
unit behave like a service, and it is **self-service — no sudo**:

```bash
loginctl enable-linger "$USER"
loginctl show-user "$USER" --property=Linger    # expect Linger=yes
```

Skip this and the entire recipe is pointless: the units come up when you log in, die when you log
out, and are absent after a reboot, which is exactly the failure you are trying to remove.

## 2. Where units live, and how to write one without corrupting it

```bash
mkdir -p ~/.config/systemd/user
```

**Build every unit file one line at a time**, first line with `>`, every line after it with `>>`,
then read it back before systemd ever sees it:

```bash
cd ~/.config/systemd/user
echo '[Unit]'                       >  <engine-unit>.service
echo 'Description=<what it serves>' >> <engine-unit>.service
# ...one echo per line...
cat <engine-unit>.service           # THE GATE: read the whole file before daemon-reload
```

> [!WARNING]
> **A long line pasted into a terminal can wrap and corrupt silently.** A wrapped `ExecStart=` gives
> you a unit that loads without complaint and launches the wrong command, or a directive systemd
> ignores. The `cat` gate is what catches it, and it is the reason the lines are written one at a
> time rather than as one heredoc.

**The workflow for every unit in this recipe is the same four steps:**

1. write it line by line;
2. `cat` it back and read it;
3. `systemctl --user daemon-reload`;
4. `systemctl --user enable --now <unit>.service`, then verify **status and a real completion**, not
   status alone.

## 3. The engine service

The shape to write:

```ini
[Unit]
Description=<model> inference server
After=network-online.target

[Service]
Type=simple
WorkingDirectory=<engine working directory>
Environment=<VAR>=<value>
ExecStart=<the exact command line the runbook launches under nohup>
Restart=always
RestartSec=30
StartLimitIntervalSec=0
TimeoutStartSec=300
KillMode=mixed

[Install]
WantedBy=default.target
```

Three of those need explaining.

- **`RestartSec=30` together with `StartLimitIntervalSec=0`.** This pairing is the whole point.
  `RestartSec=30` paces the restarts so a model that fails on load does not thrash the box in a
  crash-loop. `StartLimitIntervalSec=0` disables the start-rate limiter, so systemd **never gives up**
  and parks the unit in `failed` forever. With the limiter on, a burst of quick failures ends in a
  silent permanent death — the worst outcome, because the unit looks handled and is not. With the
  pacing off, you get a crash-loop instead. You need both.
- **`TimeoutStartSec=300`** for a light engine; **`900`** for a large model that loads slowly. Set it
  under the real load time and systemd kills the process mid-load, forever.
- **`KillMode=mixed`** so a stop signals the main process and then the whole group, which matters for
  engines that fork workers.

> [!WARNING]
> **`ExecStart=` is not a shell command line, and the launch commands in the engine runbooks are.**
> systemd does no command substitution, no `VAR=value cmd` prefix and no globbing. Paste
> `--api-key "$(cat ~/.config/qwen38/api-key)"` into `ExecStart=` and the server receives that text
> **literally** as its key — the unit reports `active (running)` and every request 401s, forever.
> Same for `EXTRA="--api-key $ENGINE_KEY" scripts/serve.sh`, whose `EXTRA=` prefix systemd treats as part
> of the program name.
>
> **Two ways out**: read the value with `EnvironmentFile=` and reference it as `$VAR` (systemd does
> expand that form), or wrap the whole thing —
> `ExecStart=/bin/sh -c '<the exact command line, quoting intact>'`. Whichever you choose, **verify by
> making a real authenticated request**, not by reading `systemctl status`.

> [!IMPORTANT]
> **A user unit does NOT inherit your shell environment.** Nothing from `~/.bashrc`, no `source`, no
> exported variable from the session you wrote the unit in. Anything the process needs must be carried
> by `Environment=` or `EnvironmentFile=`. This is the single most common reason a unit that works
> when you launch it by hand fails as a service.

## 4. Tunnel units, ordered before the gateway

One unit per tunnel, each running `ssh -N -L ...` in the foreground (no `nohup`, no `&` — systemd is
the supervisor now), with the same hardening options as the manual command in
[Tunnels](litellm-gateway-and-api-keys.md#6-tunnels-to-backend-nodes).

Order matters: a gateway that starts before its tunnels comes up cleanly and fails every request.

```ini
[Unit]
Description=SSH tunnel to <backend-node>
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -L <local-port>:localhost:<backend-port> -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=10 <user>@<backend-node>
Restart=always
RestartSec=30
StartLimitIntervalSec=0

[Install]
WantedBy=default.target
```

Then, in the **gateway** unit's `[Unit]` section, name each tunnel:

```ini
After=<tunnel-unit>.service
Wants=<tunnel-unit>.service
```

`After=` orders them; `Wants=` pulls them in without making the gateway fail if one is temporarily
down. Ordering is not the same as readiness — `Type=simple` means "started", not "forwarding" — so the
probe in section 6 is what actually catches a tunnel that came up and then broke.

## 5. The gateway service

Same shape as section 3, with two gateway-specific directives:

```ini
[Service]
WorkingDirectory=%h/llm-gateway
EnvironmentFile=%h/llm-gateway/.env
ExecStart=<path to the venv's litellm> --config config.yaml --host <vpn-address-of-this-node> --port 4000
```

- **`WorkingDirectory=%h/llm-gateway`** is required, not tidiness:
  `custom_auth: custom_auth.user_api_key_auth` is a Python import resolved from the **working
  directory**, so launching anywhere else fails with
  `ModuleNotFoundError: No module named 'custom_auth'`.
- **`EnvironmentFile=%h/llm-gateway/.env`** replaces the interactive `set -a && source .env`, which a
  unit cannot do. `%h` expands to the home directory, which keeps the username out of the file.

> [!WARNING]
> **Under a unit, the gateway's log is the systemd journal — and LiteLLM can print the master key in
> its startup banner.** There is no `StandardOutput=` above, so everything the proxy writes to stdout
> goes to your user journal instead of `~/llm-gateway/litellm.log`. Two consequences: the
> secret-scan gate in
> [§9 of the operator recipe](litellm-gateway-and-api-keys.md#9-verification-gates) must be run
> against `journalctl --user -u <gateway-unit>` rather than the file, which does not exist here; and
> **never `journalctl` this unit on a shared screen** — count with `grep -c`, as you would the file.

## 6. The liveness probe: a oneshot service plus a timer

`Restart=always` catches a process that **exits**. It does nothing about a process that is alive and
no longer serving, which is the failure that actually costs you a night.

Two units. First a `Type=oneshot` service that runs the probe script:

```ini
[Unit]
Description=Liveness probe for <engine-unit>

[Service]
Type=oneshot
ExecStart=%h/<probe-script>.sh
```

Then a timer that fires it:

```ini
[Unit]
Description=Run the liveness probe periodically

[Timer]
OnBootSec=15min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

`OnBootSec` must sit **above the engine's cold-start time**, or the first probe after every reboot
fires against a server that is still loading and logs a false failure. **15 minutes is set above
because the 180B long-context stack measures ~13½ minutes from reboot to serving.** A smaller engine
can use less — measure yours rather than trusting either number. `OnUnitActiveSec=5min` paces it afterwards,
and `WantedBy=timers.target` is what makes `enable` mean anything for a timer.

**The probe must make a real completion.** The shape of the script:

```bash
# body kept in a FILE, not inlined, so quoting cannot mangle it
curl -s --max-time 300 -o "$OUT" \
  -H "Content-Type: application/json" \
  -d @"<probe-body>.json" \
  http://127.0.0.1:<port>/v1/chat/completions
# then: check $OUT actually contains generated content, not merely that curl exited 0
```

with `<probe-body>.json` asking a trivial question of the served model.

> [!WARNING]
> **If the engine is key-authed, the probe must send the key — or it becomes a restart loop.** A probe
> without an `Authorization` header gets 401, finds no generated content, scores that as a wedge and
> restarts a perfectly healthy server. With `OnUnitActiveSec=5min` against a model that takes minutes
> to load, the endpoint then never serves again and every cycle looks like a fresh failure. Add
> `-K <mode-600 curl config>` to the `curl` above, carrying the same key the engine was launched with.
> A backend bound to loopback with no key needs nothing.

On repeated failure, log the time and the reason to a file you can read later, then:

```bash
systemctl --user restart <engine-unit>.service
```

> [!WARNING]
> **Do not probe `/health`.** An engine can wedge with its HTTP layer still answering 200 on a health
> endpoint while no request ever completes. **A real completion is the only check that distinguishes
> "serving" from "listening"**, and the probe must confirm the response carries generated content, not
> merely that curl exited 0. Bound it with `--max-time` so a wedged engine makes the probe **fail**
> rather than hang forever alongside it.

> [!WARNING]
> **Three details of that probe are engine-specific, and the obvious defaults are wrong on a reasoning
> model.** All three were measured on the 180B long-context stack:
>
> 1. **Do not ask for one token.** A reasoning model spends its budget thinking before it answers, so
>    a tight `max_tokens` returns **empty content** from a perfectly healthy server — such a probe
>    then reports failure forever. Ask for **`max_tokens: 2048`**; a trivial question still finishes
>    in a couple of hundred tokens and returns in about two seconds.
> 2. **Use `--max-time 300`, not 60, and require several consecutive failures before acting.** On a
>    loaded box a *busy* server looks exactly like a wedged one — one client prefilling a large
>    document drops everyone else to a couple of tokens per second, and real sessions have run to
>    948 s. A short-timeout probe restarts the server **because it was working**, killing a user
>    mid-request. That is worse than the wedge.
> 3. **Consider leaving the restart action switched off** until people actually depend on the
>    endpoint. Detection costs nothing, and a false positive then costs nothing either.

---

## 7. Verification: three break-tests

Our rehearsal passed all three. A unit that has not been broken on purpose is not verified.

**1. Crash — does it come back?**

```bash
systemctl --user show -p MainPID --value <engine-unit>.service    # note the PID
kill -9 <that PID>
```

Expect: the unit relaunches after the restart pacing, `MainPID` is a **new** number, and a real
completion answers. A PID that never changes means you tested nothing.

**2. Silent wedge — the differentiating test.**

```bash
kill -STOP <MainPID>     # alive, frozen, and invisible to Restart=always
```

Expect: `Restart=always` does **nothing** (correctly — the process has not exited), the next probe
times out, logs a failure, and restarts the engine. Then a completion answers again. This is the test
that justifies the probe existing; if you skip one, do not skip this one.

**3. Reboot — does it come up before you do?**

After the machine comes back, **without logging in first if you can avoid it**, check:

- the unit is `active` and its `ActiveEnterTimestamp` is at **boot time**, not at your login;
- `systemctl --user list-timers` shows the probe timer scheduled;
- a real completion answers;
- **zero manual starts** were needed.

That last point is the whole deliverable. If you had to type anything, lingering (section 1) is not
on.

---

## 8. Stopping for a maintenance window — there is a race

⚠ **Stopping the timer does not stop a probe that is already in flight.** We lost a maintenance window
to exactly this: a probe fired after what looked like a clean stop, found the engine down, and restarted
it about three minutes later. The probe log's failure line matched the restart timestamp to the second,
which is how it was caught at all.

The correct order, gating on all three checks and never on a single `is-active`:

```bash
systemctl --user stop <probe>.timer        # 1. the TIMER first
systemctl --user stop <probe>.service      # 2. AND the probe itself — stopping a timer does not
                                           #    stop a run already in flight (it has --max-time 300)
systemctl --user stop <engine-unit>.service # 3. then the SERVICE

sleep 20                                    # 4. wait, then re-verify it STAYED down
systemctl --user is-active <engine-unit>.service   # expect: inactive, still, after the wait
systemctl --user is-active <probe>.service         # expect: inactive — no probe still running

ss -tln | grep -c ":<port> "               # 5. expect 0 — the port is actually free
nvidia-smi   # or rocm-smi                 # 6. expect the model's memory actually released
```

A unit that reports `inactive` while its port is still bound or its GPU memory is still held has not
finished stopping, and starting maintenance there is how you get a half-loaded second copy.

## 9. Two benign signals, named so nobody chases them

- **`Job for <unit>.service canceled.` on stop is benign.** It means a queued job was superseded, not
  that the stop failed. Gate on `is-active`, not on that message.
- **`is-active` returns `deactivating` for several seconds** while a large engine releases memory.
  That is an unfinished stop, not a failure. Wait for `inactive` in a **bounded** loop (a fixed number
  of short sleeps, then give up and report) rather than assuming either state immediately.

> [!IMPORTANT]
> **`systemctl --user status` output and `%h`-expanded paths contain your username.** Mask them before
> pasting status output into a ticket, a chat thread or a document.

---

## 10. Teardown

```bash
systemctl --user disable --now <probe>.timer   # the timer carries the [Install], not the service
systemctl --user disable --now <probe>.service # ...and the service, in case a run is in flight
systemctl --user disable --now <gateway-unit>.service
systemctl --user disable --now <tunnel-unit>.service
systemctl --user disable --now <engine-unit>.service

rm -f ~/.config/systemd/user/<engine-unit>.service \
      ~/.config/systemd/user/<tunnel-unit>.service \
      ~/.config/systemd/user/<gateway-unit>.service \
      ~/.config/systemd/user/<probe>.service \
      ~/.config/systemd/user/<probe>.timer

systemctl --user daemon-reload
systemctl --user list-units --all | grep -c '<engine-unit>'   # expect 0
```

Optionally give lingering back as well, if nothing else on the node depends on it:

```bash
loginctl disable-linger "$USER"
```

Nothing was installed on the host, and nothing outside `~/.config/systemd/user` and your own probe
script is touched.

## 11. Not measured, and the limits of this

- **Proven on one node, against a stand-in engine.** The break-tests were real and all three passed,
  but on a single machine with a light model standing in for the production one.
- **Never rolled out to a multi-node gateway with tunnels.** The gateway and tunnel units above follow
  the same pattern as the engine unit that was tested; they are not separately break-tested.
- **The tunnel-ordering guidance is derived, not measured.** `After=`/`Wants=` is the standard way to
  express it and matches the failure we know about (a gateway with no tunnel under it fails every
  request), but no test in the rehearsal killed a tunnel and watched the ordering hold.
- **No long-run evidence.** The units were exercised across a handful of restarts and one reboot, which
  is not an uptime claim.
