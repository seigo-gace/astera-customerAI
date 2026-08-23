#!/bin/sh
set -eu

MODEL_PATH="${CUSTOMER_AI_LOCAL_MODEL_PATH:-/models/Qwen3-8B-Q4_K_M.gguf}"
MODEL_ID="${CUSTOMER_AI_LOCAL_MODEL_ID:-Qwen/Qwen3-8B}"
LLAMA_PORT="${CUSTOMER_AI_LOCAL_LLAMA_PORT:-8081}"

if [ ! -s "$MODEL_PATH" ]; then
  echo "local_model_missing=$MODEL_PATH" >&2
  exit 1
fi

/opt/llama/llama-server \
  --model "$MODEL_PATH" \
  --alias "$MODEL_ID" \
  --host 127.0.0.1 \
  --port "$LLAMA_PORT" \
  --ctx-size 4096 \
  --threads 2 \
  --threads-batch 2 \
  --parallel 1 \
  --jinja \
  > /tmp/llama-server.log 2>&1 &
LLAMA_PID=$!

ready=0
i=0
while [ "$i" -lt 600 ]; do
  if curl -fsS "http://127.0.0.1:${LLAMA_PORT}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$LLAMA_PID" 2>/dev/null; then
    echo "local_llama_server_exited" >&2
    cat /tmp/llama-server.log >&2 || true
    exit 1
  fi
  i=$((i + 1))
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "local_llama_server_not_ready" >&2
  tail -200 /tmp/llama-server.log >&2 || true
  exit 1
fi

echo "LOCAL_LLM_READY=model:${MODEL_ID}:port:${LLAMA_PORT}:provider:none"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-7860}"
