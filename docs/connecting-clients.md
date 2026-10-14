# Connecting clients

How to reach the gateway, the three values every tool asks for, per-tool setup, and the handful of
client settings that make the difference between a good experience and a confusing one.

## 1. Reach the gateway (do this first)

The gateway is reachable only over a private VPN; there is no public endpoint. Your administrator adds
your device to that network and issues you a personal **API key** and the **endpoint address**.

> [!IMPORTANT]
> **A valid API key on its own gets you nowhere.** Until your device is on the private network, the
> endpoint simply does not resolve.

**Check it worked before going further**, because every later failure looks the same from your side:

```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 10 http://<gateway-host>:4000/v1/models
```

A **401** is a **success** for this step: you reached the gateway and it asked for a key you have not
supplied yet. A timeout or "could not resolve" means you are not on the private network, and no API
key will help until that is fixed.

> [!NOTE]
> **What the setup does and does not expose.** The gateway is the front door, and it rejects any
> request without a valid key. The inference engines behind it listen on their own machines'
> loopback interfaces and are reached by the gateway, not by you, so every request goes through the
> gateway first. Nothing is published to the public internet, and keys are issued per person rather
> than shared, so one can be revoked without re-keying anyone else.
>
> **What is recorded about your use.** The gateway logs the time of each request and which key made
> it, so usage can be attributed. Prompts and replies are not written to that log. The inference
> servers keep their own operational logs, and an administrator with access to a machine can see what
> passes through it, the same position as any self-hosted service.

## 2. The three values you need

| value | what it is | example |
|---|---|---|
| `base_url` | where requests go | `http://<gateway-host>:4000/v1` |
| `api_key` | your personal key | `<YOUR_API_KEY>`; never shared or committed |
| `model` | which model to call | one of the names actually served (see §3) |

The endpoint is OpenAI-compatible, so any OpenAI SDK or OpenAI-compatible client works as a drop-in:
only the `base_url` and key change. Keep your key out of source
control and chat threads; read it from an environment variable or your tool's secret store.

**The `curl` examples on this page assume `$LLM_API_KEY` is set in your shell:**

```bash
read -rs LLM_API_KEY && export LLM_API_KEY    # prompts without echoing; no shell history
```

⚠ If you skip this, every example returns **401**. In §1, 401 meant "reached the gateway, no key
sent"; here it means your key is missing or wrong.

⚠ **On a machine other people can log into**, putting the key on the `curl` command line exposes it
in the process list. On a shared box, put the header in a mode-600 file and use `curl -K <file>`.

## 3. Which models are available

The served set changes as models are added, swapped or retired. List what is up right now:

```bash
curl http://<gateway-host>:4000/v1/models \
  -H "Authorization: Bearer $LLM_API_KEY"
```

The values you need per model, for the recipes in this repo:

| `model` | what it is | context limit | reasoning key |
|---|---|---|---|
| `qwen38` | A 27B reasoning model. The **default for agent work, coding and tool loops** | configured at 262,144, but the **measured KV pool is 196,761 tokens**, which is the real ceiling on one request; treat ~196,000 as the limit | `chat_template_kwargs` → `reasoning_effort` |
| `qwen3.8-flash-next` | A 180B model: **the long-document one** | **262,144, shared between prompt and answer**, so your prompt ceiling is roughly 262,144 minus your `max_tokens`. The largest request sent and answered correctly was **247,899** tokens; that is the largest tried, not a limit found | `chat_template_kwargs` → `reasoning_effort`, but **see the warning in §5** |
| `gemma4-26b` | A 26B model: **small and quick**, with a much shorter context | 16,384 | `chat_template_kwargs` → `enable_thinking` |

Which engine, quantisation and hardware serves each one is in
[reference/which-model-for-which-job.md](reference/which-model-for-which-job.md).

> [!IMPORTANT]
> **Model names are matched exactly.** `qwen3.8-flash-next` is lowercase and a capitalised variant is
> rejected; names must not contain slashes (`org/model-name` fails). Copy names from `/v1/models`.

> [!NOTE]
> **Model ids are chosen by whoever launches the server**, so they differ between deployments. Each
> name is a server that is actually running, usually on its own machine, so the list is a menu of
> what is served now, not a catalogue. Always confirm with the `curl` above before building against a
> name.

### Which one should I call?

- **Shared agent work, coding, tool loops, most things** → **`qwen38`**.
- **A document larger than `qwen38` will take (above ~196,000 tokens), or heavy rewriting of a long
  document** → **`qwen3.8-flash-next`**, and expect the first question to be slow (§5).
- **Short prompts, especially when several callers are asking at once** → **`gemma4-26b`**. Any
  single answer arrives more slowly than from `qwen38`, but it runs on separate hardware and is the
  configuration measured carrying the most simultaneous callers, so point batch jobs at it.

Two sizing facts you can act on:

- **Your rate drops when other people are working.** Nothing is wrong; you are sharing a machine.
- **`qwen3.8-flash-next` is for depth, not crowds.** It serves a few people at a time, and one very
  large document slows everyone else for as long as it takes to read it.

⚠ **Do not reach for the long-document model just because your document is large**: `qwen38` takes
up to ~196,000 tokens, is faster, and is built to be shared.

## 4. Per-tool setup

### Python (`openai` library)

```python
import os

from openai import OpenAI

client = OpenAI(
    base_url="http://<gateway-host>:4000/v1",
    api_key=os.environ["LLM_API_KEY"],
    timeout=1800,     # seconds; generous, not 600 (see §5)
    max_retries=0,    # let a slow call finish rather than silently re-running it
)
resp = client.chat.completions.create(
    model="<model-name>",
    messages=[{"role": "user", "content": "Reply with one word: ready"}],
    max_tokens=12000,  # derived from context minus prompt, never a round guess (see §5)
)
print(resp.choices[0].message.content)
```

### curl

```bash
curl http://<gateway-host>:4000/v1/chat/completions \
  --max-time 1800 \
  -H "Authorization: Bearer $LLM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-name>",
    "messages": [{"role": "user", "content": "Reply with one word: ready"}],
    "max_tokens": 12000
  }'
```

`max_tokens` is there even for a one-word answer because a reasoning model spends the budget
thinking first: too small a number and the budget is gone before the word arrives.

### Claude Code (Anthropic-compatible clients)

The gateway also serves an Anthropic-compatible `/v1/messages` endpoint, so Claude Code talks to it
directly. It is configured entirely through environment variables:

```bash
export ANTHROPIC_BASE_URL="http://<gateway-host>:4000"
export ANTHROPIC_AUTH_TOKEN="<YOUR_API_KEY>"
export ANTHROPIC_DEFAULT_OPUS_MODEL="<model-name>"
export ANTHROPIC_DEFAULT_SONNET_MODEL="<model-name>"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="<model-name>"
unset ANTHROPIC_API_KEY     # a leftover cloud key conflicts with ANTHROPIC_AUTH_TOKEN
claude
```

```powershell
$env:ANTHROPIC_BASE_URL="http://<gateway-host>:4000"
$env:ANTHROPIC_AUTH_TOKEN="<YOUR_API_KEY>"
$env:ANTHROPIC_DEFAULT_OPUS_MODEL="<model-name>"
$env:ANTHROPIC_DEFAULT_SONNET_MODEL="<model-name>"
$env:ANTHROPIC_DEFAULT_HAIKU_MODEL="<model-name>"
Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
claude
```

All three tier variables point at the same model because one served model answers every tier. The
status line should show `<model-name>`. Two startup messages are **expected, not errors**: a `404` on
`HEAD /` (the gateway serves nothing at bare `/`) and a "connectors disabled" notice. Claude Code sends
a large system prompt every turn, so use a model with a big context window; cap output with
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` on smaller models.

### Cursor, VS Code / Continue, GitHub Copilot

All three take the same `base_url` + `api_key` + `model` triple:

- **VS Code custom OpenAI endpoint / Continue:** endpoint `http://<gateway-host>:4000/v1`, your key,
  `<model-name>`.
- **Cursor:** Settings → Models → "Override OpenAI Base URL", add `<model-name>` as a custom model,
  paste the key into the OpenAI API key field.
- **GitHub Copilot:** "Manage Models" / custom model endpoint, same three values. See the empty
  `tool_calls` quirk in §6.

Editor chat and agent modes send large prompts (open files, repo context, history), so use a model
with at least ~32k context.

### OpenCode

OpenCode reads `opencode.json` from the **current directory**, so create it in your project and launch
from there:

```json
{
  "provider": {
    "self-hosted": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "http://<gateway-host>:4000/v1",
        "apiKey": "<YOUR_API_KEY>"
      },
      "models": {
        "<model-name>": {}
      }
    }
  }
}
```

Open the model picker with `Ctrl+X` then `M`. Invalid JSON silently fails to register the provider.
Over SSH, `Ctrl+C` exits OpenCode; copy with `Ctrl+Shift+C`.

### Open WebUI

Settings → Connections → add an **OpenAI API** connection with base URL
`http://<gateway-host>:4000/v1` and your key, save, then pick `<model-name>` from the model dropdown.
If no models appear, the base URL is usually missing its `/v1`. Some versions need the connection
toggled on after adding it.

### Hermes Agent

Choose a **custom endpoint** in its setup flow, set `base_url`, `api_key`, `model`, and API mode
**Chat Completions**. Set `context_length` **strictly below** the model's served maximum, because the
agent's own system prompt and tool definitions use part of the window. If it still overflows, trim the
toolset, but keep `file`, `terminal` and `code_execution`; without them the agent loop breaks.

## 5. Getting good results

All client-side, nothing to change on the server.

### Derive `max_tokens` from context length minus your prompt

Never pick a round number. Most of what is served here are **reasoning models**: they spend completion
budget thinking before they answer. Too small a budget and the answer never arrives: you get
`finish_reason: "length"` with empty `content`, which looks like a quality problem but is a budget
problem.

Concretely: a 16,384-token context with a ~3,100-token prompt leaves roughly 13,000 tokens. On one
schema-constrained JSON workload of exactly that shape, a `max_tokens` of 4,000 truncated about a third
of responses. ⚠ The fraction is specific to that workload; the *mechanism* transfers.

```
max_tokens = (model context length) - (your prompt tokens) - a small safety margin
```

### Set `max_retries=0` and a client timeout of at least 1800 s

The OpenAI SDK defaults to `max_retries=2` **and retries on timeout**. One slow call becomes up to
three full server-side runs, and you see a single confusing failure roughly three times longer than
your timeout while the server logs three successful requests. If you want retries, add them yourself
where you can see and log them.

### Count tokens from `usage`, never from streamed chunks

Most of these configurations run **speculative decoding**, where one streamed chunk can carry several
tokens. Counting chunks undercounts, and any tokens-per-second figure derived from chunk timings is
wrong. **Take counts from `usage.completion_tokens`.**

### `qwen3.8-flash-next`: do not trust its streaming or its rate display

This build withholds streamed output and releases it in a ~19-second tail at the end, at every depth
measured, so time-to-first-token and per-token rates derived from the stream are wrong. **If a stream
sits silent it is still working**; do not shorten your timeout in response.
For a real rate, divide the server-reported `usage.completion_tokens` by your own wall-clock time.

### Ask for lower reasoning effort when you are running an agent loop

```python
resp = client.chat.completions.create(
    model="<model-name>",
    messages=messages,
    max_tokens=12000,
    extra_body={"chat_template_kwargs": {"reasoning_effort": "low"}},
)
```

`chat_template_kwargs` is forwarded to the model unmodified. The key is model-dependent (see the table
in §3). Thinking defaults differ too: Gemma 4 has thinking **off** by default, Qwen3.8-27B **on**.

**What this buys:** on multi-turn agent loops it cuts generated tokens by **more than half**, worth up
to ~4× less waiting alone on a box and about 1.65× more work per hour with eight concurrent users. On
**hard, free-running tasks it saves close to nothing**, and on a **single question about 10%**. It is
an agent-loop lever, not a per-request one. Measurements:
[reference/numbers.md §4](reference/numbers.md#4-reasoning-effort-an-agent-loop-lever-not-a-per-request-one).

> [!WARNING]
> **Those figures are for `qwen38`, not `qwen3.8-flash-next`.** There, two attempts at
> `reasoning_effort: "low"` produced **more** tokens, not fewer (both answers correct). Its accepted
> values are `xhigh` (default), `medium` and `low`; there is no `high`. Unrecognised values are
> untested, so pass one of those three.

**Where reasoning text goes** depends on the engine: llama.cpp-served models return it in a
`reasoning_content` field, while vLLM-served Gemma 4 strips it entirely.

### Structured output (JSON schema)

`response_format` with a JSON schema is enforced by the serving engine's grammar, not by asking the
model politely.

> [!TIP]
> **Prove enforcement is on:** put an enum of nonsense values in your schema (`"ZEBRA-7"`,
> `"QUOKKA-3"`) and check the reply is one of them. A model never invents those, so seeing one
> proves the grammar is applied.

> [!WARNING]
> **Some client libraries silently downgrade structured output**: they try `json_schema`, the server
> rejects a detail, and they fall back to `json_object` and then to no constraint, without logging it.
> If conformance matters, verify server-side with the canary above.

Set `"temperature": 0` **explicitly** if you need reproducibility. Under concurrency, batched serving
can still flip an early token.

### What to expect from a long document

On `qwen3.8-flash-next`, **the first request about a document is the slowest one you will see**:

- **The first question is slow.** The server reads the whole document: roughly a minute to first
  token at 128k tokens of context, about three minutes end to end at 248k. ⚠ That is for the
  configuration behind this name; the same model on other hardware is far slower at depth.
- **Follow-ups on the same document are fast**: about an order of magnitude faster in one measured
  pair (~14.5 s cold against 1.3-1.6 s warm on a ~22,000-token document), because the server reuses
  the work it already did.
- **You may pay the slow first question more than once**, because more than one machine can serve the
  same name and each reads your document once. It is a slow question or two at the start, not a
  permanent tax.

If **every** question stays slow, say so when you report it: that means reuse is not happening.

## 6. Known tool quirks

Only failures actually seen appear here. Anything not listed is expected to work as a plain
OpenAI-compatible endpoint.

**An empty `tool_calls: []` array breaks some IDE assistants**, including GitHub Copilot chat. vLLM
streams it alongside content deltas; it is well-formed OpenAI JSON, so nothing errors, but Copilot
treats it as a tool-call turn and discards the reply text. If an IDE assistant connects, authenticates
and then goes quiet, check for empty `tool_calls` arrays. The workaround is a small proxy that strips
them from the stream before the client sees it.

**Tool calling depends on how the server was launched, not on the model.** A server started without a
tool-call parser puts tool markup in `content` as plain text and leaves `tool_calls` empty, so your
framework sees no tool call. The `gemma4-26b` recipe runs without `--tool-call-parser`, so treat tool
calling as unavailable there. For the others, check with one request:

```bash
curl http://<gateway-host>:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"<model-name>",
       "messages":[{"role":"user","content":"What is the weather in Oslo?"}],
       "tools":[{"type":"function","function":{"name":"get_weather",
         "parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}],
       "max_tokens":1024}'
```

A populated `tool_calls` array means it works; markup in `content` means it does not on that model.

**Turning on a tool-call parser later can break a client that was coping without one.** A client
reading tool markup out of `content` stops finding it there once the server emits proper
`tool_calls`.

**A sequential chain multiplies the cost of one timeout.** A framework that runs a fixed sequence
(plan, critique, decide) loses the *entire run* when any single call times out. Combined with SDK
retries, one slow call can burn many minutes and surface as one unexplained failure. Set the timeout
and `max_retries` first, and if the framework exposes debate or review rounds, **start at one**.

## 7. Troubleshooting

| symptom | almost always means |
|---|---|
| **401** | Key missing or wrong. Check the `Authorization: Bearer ...` header is actually sent. |
| **Model not found** | The name must be *currently served* and match exactly. List with `/v1/models`. |
| **Timeout, connection refused, or DNS failure** | You are not on the private network. Re-check §1 first. |
| **"It worked yesterday and now every call times out"** | If §1 checks out, the endpoint itself is down (often a machine rebooted). Nothing to fix client-side; tell your administrator. |
| **One call fails and an immediate retry works** | A configuration change briefly restarted the gateway. In-flight calls die; the next succeeds. |
| **A call that "hangs" for ~30 min then fails** | SDK retries. See `max_retries=0` in §5. |
| **Truncated or empty answers, `finish_reason: "length"`** | `max_tokens` too small for a reasoning model. See §5. |
| **Context-length errors on normal prompts** | The client's own system prompt and tools are filling the window. Use a bigger-context model or trim. |

### What to send when you report a failure

**The server cannot see your failures.** If your client gives up on a request, the server records it
as completed successfully. A timeout on your side is a success in the server logs, so "it is failing"
cannot be investigated from server logs alone. Send four things:

| what | why it matters |
|---|---|
| `finish_reason` on a failing call | `length` = budget ran out; `stop` = model finished, problem is elsewhere; absent = never completed |
| `usage.completion_tokens` | separates a budget problem from a model problem |
| your client timeout | the single most common cause |
| your `max_retries` | decides whether one failure you see is one request or three |
