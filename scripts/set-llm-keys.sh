#!/bin/bash
# Set LLM/Langfuse keys in the DosaDash .env — works two ways:
#   * ON the VPS  (bash set-llm-keys.sh)        → edits /opt/dosadash/infra/.env directly
#   * from a laptop (bash scripts/set-llm-keys.sh) → same edit over SSH ($DOSADASH_HOST, default dosadash-prod)
# Prompts are silent (keys never echo, never land in shell history). Idempotent.
# Keys take effect on the next deploy (the Phase 2 compose passes them to the ai service).

set -euo pipefail
HOST="${DOSADASH_HOST:-dosadash-prod}"
ENV_FILE="${DOSADASH_ENV_FILE:-/opt/dosadash/infra/.env}"

if [ -w "$ENV_FILE" ]; then MODE="local"; else MODE="ssh"; fi
echo "mode: $MODE — target: $ENV_FILE"

read -r -s -p "OPENAI_API_KEY (required): " OPENAI_KEY; echo
read -r -s -p "GROQ_API_KEY (optional, Enter to skip): " GROQ_KEY; echo
read -r -s -p "GEMINI_API_KEY (optional, Enter to skip): " GEMINI_KEY; echo
read -r -s -p "LANGFUSE_PUBLIC_KEY (optional): " LF_PUB; echo
read -r -s -p "LANGFUSE_SECRET_KEY (optional): " LF_SEC; echo

[ -n "$OPENAI_KEY" ] || { echo "OPENAI_API_KEY is required"; exit 1; }

set_kv() {  # set_kv KEY VALUE — replace-or-append, skips empty values
  local key="$1" val="$2"
  [ -n "$val" ] || return 0
  if [ "$MODE" = "local" ]; then
    sed -i "/^[# ]*${key}=/d" "$ENV_FILE"
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  else
    # shellcheck disable=SC2029
    ssh "$HOST" "sed -i '/^[# ]*${key}=/d' '$ENV_FILE' \
      && printf '%s=%s\n' '$key' '$val' >> '$ENV_FILE'"
  fi
}

set_kv OPENAI_API_KEY      "$OPENAI_KEY"
set_kv GROQ_API_KEY        "$GROQ_KEY"
set_kv GEMINI_API_KEY      "$GEMINI_KEY"
set_kv LANGFUSE_PUBLIC_KEY "$LF_PUB"
set_kv LANGFUSE_SECRET_KEY "$LF_SEC"
set_kv LANGFUSE_HOST       "https://cloud.langfuse.com"

if [ "$MODE" = "local" ]; then
  chmod 600 "$ENV_FILE"
  COUNT=$(grep -cE '^(OPENAI|GROQ|GEMINI|LANGFUSE)' "$ENV_FILE")
else
  COUNT=$(ssh "$HOST" "chmod 600 '$ENV_FILE' && grep -cE '^(OPENAI|GROQ|GEMINI|LANGFUSE)' '$ENV_FILE'")
fi
echo "done — $COUNT LLM/tracing keys present in $ENV_FILE (values not shown)"
echo "They take effect when the Phase 2 release deploys (compose wires them into the ai service)."
