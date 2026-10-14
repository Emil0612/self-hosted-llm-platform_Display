#!/usr/bin/env bash
# Liveness = a real completion, not an open port or /health: an engine can keep
# answering health checks while no request ever completes.
#
# Rules from docs/runbooks/gateway/durable-gateway-user-service.md section 6:
#  - ask for enough tokens (2048): a reasoning model thinks first, and a tiny budget
#    returns empty content from a perfectly healthy server
#  - allow 300 s and require several consecutive failures: a busy server looks like
#    a wedged one, and restarting it kills real requests
#  - check for generated content, not just that curl succeeded
#  - if the engine needs a key, send it (add -K <mode-600 curl config>), or every
#    probe gets 401 and the server is restarted forever
set -u
PORT=8000
UNIT=engine.service
STATE="$HOME/.cache/engine-probe.failures"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT

curl -s --max-time 300 -o "$OUT" \
  -H 'Content-Type: application/json' \
  -d '{"model":"<model-name>","messages":[{"role":"user","content":"Reply with the word OK."}],"max_tokens":2048}' \
  "http://127.0.0.1:${PORT}/v1/chat/completions"

if grep -q '"content": *"[^"]' "$OUT"; then
  echo 0 > "$STATE"
  exit 0
fi

fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$fails" > "$STATE"
echo "$(date -Is) probe failed (${fails} in a row)" >> "$HOME/.cache/engine-probe.log"
if [ "$fails" -ge 3 ]; then
  echo 0 > "$STATE"
  systemctl --user restart "${UNIT}"
fi
