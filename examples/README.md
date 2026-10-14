# Examples

Placeholder versions of the configuration the runbooks build step by step. Nothing here contains a
real host, key or address; every `<...>` is yours to fill in.

| File | What it is | Explained in |
|---|---|---|
| `gateway/config.example.yaml` | LiteLLM model groups: one public name, several backends | [gateway runbook §3](../docs/runbooks/gateway/litellm-gateway-and-api-keys.md#3-configyaml-a-model-name-mapped-to-backends) |
| `gateway/custom_auth.py` | Static per-user keys, scoped to models, with per-key access logging | [gateway runbook §4](../docs/runbooks/gateway/litellm-gateway-and-api-keys.md#4-custom_authpy-master-key-plus-scoped-per-consumer-keys) |
| `systemd/llm-gateway.service` | The gateway as a no-sudo user service | [durable gateway §5](../docs/runbooks/gateway/durable-gateway-user-service.md#5-the-gateway-service) |
| `systemd/engine.service` | An inference engine that restarts when it exits | [durable gateway §3](../docs/runbooks/gateway/durable-gateway-user-service.md#3-the-engine-service) |
| `systemd/engine-probe.*` | A timer that sends a real completion and restarts the engine if it gets none | [durable gateway §6](../docs/runbooks/gateway/durable-gateway-user-service.md#6-the-liveness-probe-a-oneshot-service-plus-a-timer) |

## Environment variables the gateway reads

Keep these in a file next to `config.yaml`, readable only by you (`chmod 600`), referenced by
`EnvironmentFile=` in the unit, and never committed:

| Variable | Purpose |
|---|---|
| `LITELLM_MASTER_KEY` | Admin key; can call every model |
| `USER_A_KEY`, `USER_B_KEY`, ... | One key per user, scoped in `custom_auth.py` |
| `BACKEND_KEY` | Key for a backend engine that requires one |

Generate keys on the server so they never pass through a terminal you share, for example
`sk-$(openssl rand -hex 24)`.
