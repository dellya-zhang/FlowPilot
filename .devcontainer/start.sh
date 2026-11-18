#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-mock}"
case "$mode" in
  mock) ;;
  deepseek)
    if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
      echo 'Configure DEEPSEEK_API_KEY as a Codespaces secret, or enter it in this terminal using read -rsp "DeepSeek API key: " DEEPSEEK_API_KEY; export DEEPSEEK_API_KEY'
      exit 1
    fi ;;
  *) echo 'Usage: bash .devcontainer/start.sh [mock|deepseek]'; exit 1 ;;
esac
mkdir -p artifacts
pidfile=artifacts/codespace-server.pid
if [[ -s "$pidfile" ]]; then
  pid="$(cat "$pidfile")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && [[ -r "/proc/$pid/cmdline" ]] && grep -azq 'app.main:app' "/proc/$pid/cmdline"; then
    kill "$pid" || true
    for i in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep .2; done
  fi
fi
# Explicit mode; no paid requests during Codespace startup. Keep the forwarded port private.
export AI_PROVIDER="$mode" DATABASE_URL="sqlite+aiosqlite:///./artifacts/codespace.db" ARTIFACT_DIR="./artifacts" LLM_AUDIT_PATH="./artifacts/llm-usage.jsonl"
export REDIS_URL="" S3_ENDPOINT_URL="" CLICKHOUSE_URL="" ACCESS_TOKEN=""
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >artifacts/codespace-server.log 2>&1 </dev/null &
echo "$!" >"$pidfile"
for i in {1..60}; do
  if ! kill -0 "$!" 2>/dev/null; then break; fi
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
    if [[ -n "${CODESPACE_NAME:-}" && -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]]; then
      echo "Open the PRIVATE forwarded port: https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/console/"
    else
      echo 'Open http://localhost:8000/console/'
    fi
    echo "Model mode: $mode"
    exit 0
  fi
  sleep 1
done
tail -n 60 artifacts/codespace-server.log
exit 1
