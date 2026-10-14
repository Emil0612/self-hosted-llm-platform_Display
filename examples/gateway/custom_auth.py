"""Static per-user API keys for LiteLLM, without a database.

The master key can call every model; each user key is scoped to the models
listed for it. Keys come from environment variables, never from this file.
Every successful authentication is appended to access.log as "timestamp label",
so usage can be attributed per key without turning on verbose logging.
"""
import os
import time

from fastapi import HTTPException, Request
from litellm.proxy._types import UserAPIKeyAuth

MASTER = os.environ["LITELLM_MASTER_KEY"]

# label -> (environment variable holding the key, models that key may call)
USERS = {
    "user-a": ("USER_A_KEY", ["<public-model-name>"]),
    "user-b": ("USER_B_KEY", ["<public-model-name>", "<keyed-model-name>"]),
}
KEYS = {os.environ[var]: (label, models) for label, (var, models) in USERS.items()}


def _log(label):
    try:
        with open("access.log", "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), label))
    except OSError:
        pass                      # a logging failure must never break authentication


async def user_api_key_auth(request: Request, api_key: str) -> UserAPIKeyAuth:
    k = (api_key or "").replace("Bearer ", "").strip()   # no header -> api_key is None
    if k == MASTER:
        return UserAPIKeyAuth(api_key=k)
    if k in KEYS:
        label, models = KEYS[k]
        _log(label)
        return UserAPIKeyAuth(api_key=k, models=models)
    raise HTTPException(status_code=401, detail="Invalid API key")   # 401, not a plain exception (500)
