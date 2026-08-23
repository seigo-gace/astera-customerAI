#!/bin/sh
set -eu

MODEL_4B="${CUSTOMER_AI_LOCAL_4B_PATH:-/models/llm-jp-3-3.7b-instruct3-Q4_K_M.gguf}"
MODEL_8B="${CUSTOMER_AI_LOCAL_8B_PATH:-/models/llm-jp-4-8b-instruct-Q4_K_M.gguf}"

for path in "$MODEL_4B" "$MODEL_8B"; do
  if [ ! -s "$path" ]; then
    echo "local_model_missing=$path" >&2
    exit 1
  fi
done

start_server() {
  name="$1"
  model="$2"
  alias="$3"
  port="$4"
  threads="$5"

  /opt/llama/llama-server \
    --model "$model" \
    --alias "$alias" \
    --host 127.0.0.1 \
    --port "$port" \
    --ctx-size 2048 \
    --threads "$threads" \
    --threads-batch "$threads" \
    --parallel 1 \
    --jinja \
    > "/tmp/${name}.log" 2>&1 &
  echo $!
}

# Required topology: two independent 4B runtimes plus one independent 8B runtime.
PID_CONSTRUCTIVE=$(start_server constructive "$MODEL_4B" "llm-jp/llm-jp-3-3.7b-instruct3" 8081 1)
PID_ADVERSARIAL=$(start_server adversarial "$MODEL_4B" "llm-jp/llm-jp-3-3.7b-instruct3" 8082 1)
PID_EVIDENCE=$(start_server evidence "$MODEL_8B" "llm-jp/llm-jp-4-8b-instruct" 8083 1)

check_alive() {
  pid="$1"
  name="$2"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "local_llama_server_exited=$name" >&2
    tail -200 "/tmp/${name}.log" >&2 || true
    exit 1
  fi
}

ready=0
i=0
while [ "$i" -lt 900 ]; do
  check_alive "$PID_CONSTRUCTIVE" constructive
  check_alive "$PID_ADVERSARIAL" adversarial
  check_alive "$PID_EVIDENCE" evidence
  if curl -fsS http://127.0.0.1:8081/health >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8082/health >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8083/health >/dev/null 2>&1; then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "local_role_models_not_ready" >&2
  for name in constructive adversarial evidence; do
    echo "--- ${name} ---" >&2
    tail -100 "/tmp/${name}.log" >&2 || true
  done
  exit 1
fi

echo "LOCAL_ROLE_MODELS_READY=constructive:4B@8081,adversarial:4B@8082,evidence:8B@8083,provider:none"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-7860}"
