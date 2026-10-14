# Adding another DGX Spark node

```text
Verified against : DGX Spark class node (NVIDIA GB10, sm_121, arm64, 128 GB unified)
Engine / build   : SGLang v1.5.9 pinned image, Qwen3.8-27B-NVFP4 rev 52d1adc, z-lab DFlash2 drafter
Date verified    : 2026-09-07 (first unit) · 2026-09-14 (§3 firmware and §4 thermals replayed
                   on a second unit of this class)
```

> [!WARNING]
> **This runbook was written from the build of the FIRST unit of this class on the platform.** Most of
> it has still been performed once, on one machine; the ordering and the gates are ours, but the
> "it works the same on a fresh box" claim is largely untested. Expect firmware and BIOS differences
> between units.
>
> **Partially re-run on a second unit, 2026-09-14:** §3 (BIOS and firmware, which matched) and §4
> (thermal characterisation, which passed) were replayed. §4's "what healthy looks like" table is
> still the first unit's figures. Everything else remains single-unit. Update this header as you
> replay more of it.

---

## 1. ★ Start here: you do not need a cable

**No interconnect is required to add serving capacity.** The QSFP / ConnectX-7 link exists for
**tensor-parallel pairing** of two units into one logical server. That is a different thing from
running two servers, and for our workload it is usually the worse thing.

What the evidence says:

- **From roughly 8 concurrent sessions upward, two INDEPENDENT nodes beat a tensor-parallel pair.**
  A published concurrency study measured independents at ~285 t/s aggregate (c=4 each) against
  ~199 t/s for the TP2 pair at c=8; two TP2 replicas also beat TP4 at c=32.
- **A single node WITH the DFlash2 drafter is roughly equal to a TP2 pair WITHOUT it** (~47.9 vs
  ~51.8 t/s, **both third-party cells; neither is ours**). If that relationship holds on our kit it
  would make the speculative decoder worth close to a second box — but our own single-node agentic
  band is ~35-41 t/s on a different workload, so we have not demonstrated it.
- TP2's advantage is the **single-stream latency race** (~1.4×), and nobody has published TP2 with
  DFlash2 in thinking mode. Our own agentic estimate for a pair is ~55-70 t/s (moderate confidence
  at the low end, low confidence at the top).
- Pooled 256 GB context for a 27B model has no published win, since pooled-memory content targets
  235B+ models.

⇒ **Default shape: two independent nodes, each running the full pinned stack**, behind
`sglang-router` for cache-aware prefix routing (plain round-robin load balancing destroys radix
prefix locality, a documented effect, and radix locality is what buys the 0.14-0.41 s TTFT), with a
LiteLLM gateway in front for API keys and quotas. **Zero new software on the nodes themselves.**

Keep TP2 in reserve as an optional re-cabled low-latency lane if a specific single-stream latency
gate fails on one box. That is a recabling decision, not a prerequisite.

### About the ConnectX-7 NIC

The CX-7 on this hardware class is **hot-plug: it does not enumerate in `lspci` until a QSFP cable
is inserted.** An empty `lspci` is not evidence that the NIC is absent.

```bash
# The NIC is present even when lspci shows nothing:
dmesg | grep -c mlx5_core          # expect a non-zero count (4 PCI functions on our unit)
ls /etc/nvidia/cx7-hotplug-enabled # exists on a unit with hotplug enabled
```

Removing `/etc/nvidia/cx7-hotplug-enabled` and rebooting forces the NIC permanently visible, at the
cost of roughly doubling idle power. Ops notes if you do cable a pair: 27 W PCIe power warnings are
documented, and a Feb-2026 firmware update itself introduced a CX-7 hotplug disappearance bug.

---

## 2. Join the private network

Bring the unit onto the platform's private VPN before anything else, so the rest of the
bring-up can be done over it. Every engine on the platform binds `127.0.0.1` and is reached over the
private network or a tunnel; nothing listens on a public interface.

Gate: the new node can reach `<gateway-host>` and an admin workstation can reach the new node.

## 3. Record BIOS and firmware before you touch anything

**Units vary**, and thermal behaviour on this hardware class is firmware-sensitive. Record what you
have so a later difference in numbers has somewhere to land:

```bash
sudo dmidecode -s bios-version
uname -r
nvidia-smi --query-gpu=driver_version,compute_cap --format=csv
```

For reference, the first unit on the platform reported BIOS version 0105 (dated
05/05/2026), kernel `6.17.0-1031-nvidia`, driver `580.173.02`, CUDA 13.0.88, compute capability
`12.1`. The community baseline discussed for the throttle issues is older (BIOS v0103, PD 0x507),
and the OEM specifically recommends a "double flash" of the PD controller firmware on units showing
the fault. If your unit is older than the above, decide about firmware **before** you benchmark,
not after.

### ⚠ Check the desktop power settings, even on a machine you will only ever use over SSH

These units ship with a **full graphical desktop and a display manager running**, not the headless
server install you might assume. That matters for one reason:

```bash
gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type
```

If that returns `'suspend'`, the desktop is configured to **suspend the whole machine after an idle
period** — which on an inference node would take a resident model and every in-flight request with
it. It read `'suspend'` as shipped on **both units we checked** (2026-09-14).

⚠ **We have not observed a suspend, and we did not test what triggers one.** What we established is a
settings readout, not a reproduction: we do not know whether the timer requires a console login, an
active graphical session, or neither. That uncertainty is the argument for changing it rather than
against — the setting is a hazard we removed, not a fault we diagnosed.

```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
```

⚠ These are **per-user** settings, so they apply to the account that runs them. If more than one
person can log in at the console, set them for each, or take the harder guarantee:
`sudo systemctl mask suspend.target`. **Do this on a new node before it serves anything**, because
the symptom — a machine that is simply gone, with a clean journal — is indistinguishable from the
power fault described in the folder README, and you will spend the afternoon on the wrong one.

## 4. Characterise thermals and clocks under load, BEFORE any benchmark

Thermal throttling on this hardware class is documented and partly unresolved: reports of units
capping well below the 240 W module rating, ACPI zones at 96-97 °C with fans not ramping, and
spontaneous reboots under sustained load. **A throttled box silently halves every number.** Our
sustained-load use case — multi-user serving, and unattended agent loops run overnight — is exactly
where the reports cluster.

> [!IMPORTANT]
> **The check must be BEHAVIOURAL, not a power reading.** `nvidia-smi` on GB10 reports **no power
> limit at all** (Current / Requested / Default / Min / Max all `N/A`), and **hwmon exposes no
> power sensor**: only `acpitz`, `nvme` and the wifi PHY appear. There is no `tegrastats` and no
> `nvpmodel`. The same GPU under comparable sustained load read 40.0-49.5 W in one session and a
> maximum of 9.69 W in another, and that spread is an instrument artifact. Watch clocks and event
> reasons instead.

```bash
# Sample every 5 s while a real sustained load runs in another shell.
nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,temperature.gpu,clocks_event_reasons.active \
           --format=csv,noheader -l 5 > ~/clk.txt
```

Run it against **minutes** of continuous inference, not seconds. Our first attempt was under 3
seconds of GPU time and peaked at 13.42 W, so it proved nothing.

**What healthy looks like** (our unit, across a 12m43s bring-up load and a separate 40-minute /
480-sample concurrency run):

| signal | healthy |
|---|---|
| SM clocks | **2385-2418 MHz** held, against a 3003 MHz ceiling; under 1% variation, no sag |
| `clocks_event_reasons.active` | **`0x0` on every loaded sample** (480/480) |
| temperature | ramps from ~36 °C, **plateaus 70-73 °C** |
| thermal slowdown counters | **0 µs** since boot |
| reboots under load | none |

Two notes. `SW Power Cap` (`0x4`) flickering at idle in P8 is ordinary power management, not the
defect. And **this clears a unit for inference, not for its full power envelope**, because
bandwidth-bound decode never demands the 240 W module rating, so a compute-saturating
characterisation (the load a fine-tune produces) has **not been run** on any unit we operated.

## 5. Docker and group membership

```bash
sudo usermod -aG docker <admin-user>
newgrp docker
docker run --rm hello-world     # must be an aarch64 image
```

⚠ Everything on this box must be **aarch64**. x86 images do not run at all. Docker group membership
is **root-equivalent** on a single-user box, so grant it deliberately. Undo:
`sudo gpasswd -d <admin-user> docker`.

## 6. Run the pinned installer

Full detail, flags and the two hard safety limits are in
[qwen38-27b-sglang-nvfp4-dflash2.md](qwen38-27b-sglang-nvfp4-dflash2.md). In short, it builds the
serving image on the node, fetches the `RadixArk/Qwen3.8-27B-NVFP4` target at revision `52d1adc`
and the `z-lab/Qwen3.8-27B-DFlash2` drafter, then installs the systemd units.

```bash
git clone https://github.com/hasso5703/dgx-spark-qwen38 ~/dgx-spark-qwen38
less ~/dgx-spark-qwen38/install.sh     # read it before running it
bash ~/dgx-spark-qwen38/install.sh
```

**Your choice:** the clone destination `~/dgx-spark-qwen38` is illustrative. Check upstream for a
newer recipe version before installing, and see the
[flagship recipe](qwen38-27b-sglang-nvfp4-dflash2.md) for the full install walkthrough.

**Pin identically to the existing node.** Installer version, model revision, drafter, and every
serve flag: `--spec-draft-length 8`, `--mem-fraction-static 0.50`, `--context-length 262144`,
`--max-running-requests 8`, `--disable-flashinfer-autotune`, Docker caps `--memory 100g
--memory-swap 100g --shm-size 16g`. Two nodes that differ in any of these are not a fleet, they are
two experiments.

> [!NOTE]
> **The serving image is built on each node, so there is no registry digest to match against.**
> `docker images --digests` reports `<none>` for it. Pin the **installer version**, and match the
> **base image digest recorded in the [flagship recipe](qwen38-27b-sglang-nvfp4-dflash2.md)** —
> that base digest is the thing two nodes genuinely share — then let the installer produce the local
> image. To confirm two nodes ended up on the same build, compare the image tag each is running:
>
> ```bash
> docker ps --format '{{.Names}} {{.Image}}'
> ```

> [!WARNING]
> Do not raise `--mem-fraction-static` past 0.50 outside the Docker caps. A host freeze that kills
> SSH and needs a power cycle is documented on this stack, because unified memory lets the engine
> starve the host rather than only itself.

> [!WARNING]
> Keep the KV cache at bf16. fp8 KV plus DFlash2 produces infinite repetition; it is a structural
> incompatibility, not a tuning choice.

## 7. Key file and units

The real key lives at `~/.config/qwen38/api-key`, mode `600`; the unit reads it as
`--api-key "$(cat …)"`, so **rotation is write-the-file plus restart**. Note the installer also
writes an `opencode.json` containing a *different* value that the server rejects with 401, so raw
clients must read the key file.

Give the new node **its own key**, or issue keys at the gateway and let the gateway hold one
per-node credential. Do not copy the first node's key across just to save a step.

```bash
sudo systemctl enable --now qwen38-sglang qwen38-keepalive
```

If you ever need to repair a broken unit file, **regenerate it from the installer's own template**
rather than patching the live file with `sed`, which is how this service gets taken down.

## 8. Verification gates

Gate on the **HTTP status code**. SGLang's `/health` returns 200 with an **empty body**, so a body
grep spins forever; and a port check is wrong in the other direction on engines that bind before
loading.

```bash
until [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:30000/health)" = "200" ]; do
  sleep 5
done; echo SERVER-UP

umask 077
printf 'header = "Authorization: Bearer %s"\n' "$(cat ~/.config/qwen38/api-key)" > ~/.q38-curlauth
curl -s -o /dev/null -w 'AUTH=%{http_code}\n' -K ~/.q38-curlauth \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  http://127.0.0.1:30000/v1/chat/completions

curl -s -o /dev/null -w 'NOAUTH=%{http_code}\n' -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  http://127.0.0.1:30000/v1/chat/completions
```

Expected: `SERVER-UP`, `AUTH=200`, `NOAUTH=401`. Remove the auth file afterwards
(`rm -f ~/.q38-curlauth`).

> [!IMPORTANT]
> **The key goes in a mode-600 curl config, not on the command line.** `-H "Bearer $(cat keyfile)"`
> is expanded by the shell before `curl` runs, so the live secret sits in the process table where any
> local user can read it. `printf` is a shell builtin, so it forks nothing and exposes no argument.

The served model id `qwen38` is confirmed: the
concurrency harness sent exactly that string to the live server and it was accepted. For 5-7
minutes after a restart the unit shows `active` while health returns `000`, because the port is
unbound until the model has loaded. That is normal.

Also confirm the KV pool matches the other node: `max_total_num_tokens` should read **196,761** at
mem-fraction 0.50 on this config. A different pool size means a different effective machine, and
the per-request-context table in the flagship recipe no longer applies.

## 9. ⚠ Byte-compare the model files against the existing node

**Do this before you compare a single number between the two machines.**

```bash
# on each node, for each weight file and the drafter
wc -c <path-to-checkpoint-file>
```

This is not paranoia. A publisher re-uploaded a quantized checkpoint under the **same repository,
same filename**, and a fresh pull came down 364 MB smaller than the copy already on the first node
(17,559,178,144 B vs 17,923,394,624 B, and the drafter differed too, by 64 bytes). A cross-machine
comparison made against those two files is measuring the checkpoint, not the hardware. An
"identical model, identical result" claim built on unverified files does not survive the check.

**Rule: byte-compare model files across machines before any cross-machine measurement cell.**
Pinning a revision hash protects you going forward; `wc -c` is what catches what you already have.

## 10. Baseline the new node on the same instrument

Run the same replay or ladder used on the first node, on the same day if you can. Two things to
check before you believe any comparison:

- **Acceptance.** Spec-decode acceptance on real agentic content runs ~4.0 of 8 draft tokens
  (median 4.03 over 339 batches on our unit). A rate without an acceptance figure is not comparable
  across nodes or sessions.
- **The day band.** The replay instrument carries a **±10% day/composition band**: identical
  config and task measured ~40.8 pooled on one day and ~35.5-37.7 on another, with a fresh-server
  control ruling out uptime and cache state. A new node reading within that band of the existing
  one is a match, not a difference. Anything you want to claim under 10% needs same-day interleaved
  controls on both boxes.

Expected shape for a healthy node on the pinned stack: **~35-41 t/s on agentic replay, 42-50 on
code generation, ~25 on prose, TTFT 0.14-0.41 s on warm turns**, and at 8 concurrent agentic
sessions **~24-27 t/s per stream / ~162 t/s aggregate steady-state** at ~6.5k context.

---

## 11. Routing the pair

Once both nodes pass their gates:

1. **`sglang-router` in front of the two servers**, using cache-aware prefix routing. Plain
   round-robin loses radix locality, which is what buys the sub-second TTFT.
2. **LiteLLM in front of the router** for API keys, quotas and per-user model names. One key
   for a user works identically whichever node serves them. See
   [litellm-gateway-and-api-keys.md](../gateway/litellm-gateway-and-api-keys.md).
3. `chat_template_kwargs` (the reasoning-effort selector) is **forwarded unmodified** through
   LiteLLM to an OpenAI-compatible backend, tested, with no `allowed_openai_params` needed. Effort
   modes survive the gateway.

## 12. What we did not measure

- Two units have not been run as a pair, in either mode, by us. The TP2 and independent-pair
  figures quoted at the top are third-party.
- The 240 W power envelope under compute-saturating load, on any unit.
- Concurrency beyond 8 in-flight sessions, and concurrency at repo-worker context depth (20-30k+),
  which is pool-bound rather than slot-bound.
- Any soak or uptime claim.

---

## 13. Teardown: returning a second node to stock

This runbook installs nothing of its own. Everything that changes state on the new box comes from
the pinned installer, so the undo is the flagship recipe's teardown, run on that node:

```bash
sudo systemctl disable --now qwen38-sglang qwen38-keepalive
sudo rm /etc/systemd/system/qwen38-*.service
sudo systemctl daemon-reload
docker image rm <local-image-tag>     # read the tag off `docker images` first
sudo gpasswd -d <admin-user> docker        # only if the group was added for this
```

Then remove the node from the router and the gateway pool and revoke its key, so nothing keeps
sending it traffic.

⚠ **Step 2 does need an undo, and it is the one that matters.** Joining the private VPN enrolled
this box as an authenticated peer, and that survives everything above: a unit handed back, RMA'd or
repurposed while still enrolled can still reach the platform. **Remove the peer from the VPN's admin
console and uninstall the client on the box**, then confirm from the node that `<gateway-host>` no
longer resolves. Step 4 (characterising thermals) changes nothing and needs no undo.
⚠ **§3 does change the box**: the four `gsettings` values, and `systemctl mask suspend.target` if you
took that option. Reverse them explicitly when returning a unit to stock — a masked `suspend.target`
is the one that will confuse whoever gets it next. The only other reversible change this runbook mentions
is deleting `/etc/nvidia/cx7-hotplug-enabled`; if you did that, restoring the file and rebooting
returns the NIC to hot-plug behaviour and idle power to normal.

---

**See also.** The baseline in section 10 only means something read against
[how-to-read-these-numbers.md](../../reference/how-to-read-these-numbers.md), whose conventions the
expected shape above assumes, and against [numbers.md](../../reference/numbers.md), where the first
node's cells are recorded. What the new node should then serve is settled in
[which-model-for-which-job.md](../../reference/which-model-for-which-job.md), and
[the gateway runbooks](../gateway/README.md) are how users reach it once it does.
