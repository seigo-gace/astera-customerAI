FROM ghcr.io/ggml-org/llama.cpp:server AS llama_cpp

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    LD_LIBRARY_PATH=/opt/llama \
    CUSTOMER_AI_HF_API_URL=http://127.0.0.1:8081/v1/chat/completions \
    CUSTOMER_AI_LOCAL_MODEL_ID=Qwen/Qwen3-8B \
    CUSTOMER_AI_LOCAL_MODEL_PATH=/models/Qwen3-8B-Q4_K_M.gguf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=llama_cpp /app /opt/llama

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

# Pin the official Apache-2.0 Qwen3-8B GGUF used for free local CPU inference.
# The model is embedded into the Space image so runtime does not call HF Inference Providers.
RUN mkdir -p /models \
    && python - <<'PY'
from hashlib import sha256
from pathlib import Path
from huggingface_hub import hf_hub_download

repo_id = "Qwen/Qwen3-8B-GGUF"
revision = "6a569868d07d3bd59e8b97fb001bf8c0b254bb20"
filename = "Qwen3-8B-Q4_K_M.gguf"
expected = "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"
path = Path(hf_hub_download(repo_id=repo_id, revision=revision, filename=filename, local_dir="/models"))
h = sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        h.update(chunk)
actual = h.hexdigest()
if actual != expected:
    raise SystemExit(f"qwen3_8b_gguf_sha_mismatch expected={expected} actual={actual}")
print(f"LOCAL_MODEL_READY={path} SHA256={actual}")
PY

COPY app.py /app/app.py
COPY runtime /app/runtime
COPY config /app/config
COPY scripts/start_local_cpu.sh /app/scripts/start_local_cpu.sh
RUN chmod +x /app/scripts/start_local_cpu.sh

EXPOSE 7860

CMD ["/app/scripts/start_local_cpu.sh"]
