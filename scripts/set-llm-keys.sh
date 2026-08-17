#!/bin/bash
# Update LLM/Langfuse keys in /opt/dosadash/infra/.env on the VPS.
# Usage:  bash scripts/set-llm-keys.sh
# Prompts locally (silent input), writes idempotently over SSH (no vi needed).
# Keys take effect on the next deploy (compose passes them to the `ai`
# service from the Phase 2 release onward). Safe to run repeatedly.

set -euo pipefail
HOST="${DOSADASH_HOST:-dosadash-prod}"
ENV_FILE="/opt/dosadash/infra/.env"

read -r -s -p "OPENAI_API_KEY (required): " OPENAI_KEY; echo
read -r -s -p "GROQ_API_KEY (optional, Enter to skip): " GROQ_KEY; echo
read -r -s -p "GEMINI_API_KEY (optional, Enter to skip): " GEMINI_KEY; echo
read -r -s -p "LANGFUSE_PUBLIC_KEY (optional): " LF_PUB; echo
read -r -s -p "LANGFUSE_SECRET_KEY (optional): " LF_SEC; echo

[ -n "$OPENAI_KEY" ] || { echo "OPENAI_API_KEY is required"; exit 1; }

set_kv() {  # set_kv KEY VALUE — replace-or-append, skips empty values
  local key="$1" val="$2"
  [ -n "$val" ] || return 0
  # shellcheck disable=SC2029
  ssh "$HOST" "sed -i '/^[# ]*${key}=/d' '$ENV_FILE' \
    && printf '%s=%s\n' '$key' '$val' >> '$ENV_FILE'"
}

set_kv OPENAI_API_KEY   "$OPENAI_KEY"
set_kv GROQ_API_KEY     "$GROQ_KEY"
set_kv GEMINI_API_KEY   "$GEMINI_KEY"
set_kv LANGFUSE_PUBLIC_KEY "$LF_PUB"
set_kv LANGFUSE_SECRET_KEY "$LF_SEC"
set_kv LANGFUSE_HOST    "https://cloud.langfuse.com"

ssh "$HOST" "chmod 600 '$ENV_FILE' && grep -cE '^(OPENAI|GROQ|GEMINI|LANGFUSE)' '$ENV_FILE'" \
  | xargs -I{} echo "done — {} LLM/tracing keys present in $ENV_FILE (values not shown)"
echo "They will be picked up by the ai service on the next deploy (Phase 2 → main)."
