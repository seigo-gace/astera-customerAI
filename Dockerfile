FROM ghcr.io/ggml-org/llama.cpp:server AS llama_cpp

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860 \
    LD_LIBRARY_PATH=/opt/llama \
    CUSTOMER_AI_CONSTRUCTIVE_API_URL=http://127.0.0.1:8081/v1/chat/completions \
    CUSTOMER_AI_ADVERSARIAL_API_URL=http://127.0.0.1:8082/v1/chat/completions \
    CUSTOMER_AI_EVIDENCE_API_URL=http://127.0.0.1:8083/v1/chat/completions \
    CUSTOMER_AI_LOCAL_4B_PATH=/models/llm-jp-3-3.7b-instruct3-Q4_K_M.gguf \
    CUSTOMER_AI_LOCAL_8B_PATH=/models/llm-jp-4-8b-instruct-Q4_K_M.gguf

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=llama_cpp /app /opt/llama

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

# Download immutable, public GGUF weights at build time. These are local model
# files, not Hugging Face Inference Provider calls, so runtime inference credits
# are never consumed.
RUN mkdir -p /models \
    && python - <<'PY'
from hashlib import sha256
from pathlib import Path
from huggingface_hub import hf_hub_download

targets = [
    {
        "repo": "mmnga/llm-jp-3-3.7b-instruct3-gguf",
        "revision": "7edef5a4f094ec8c1aed1e196c6a544675efbc2f",
        "filename": "llm-jp-3-3.7b-instruct3-Q4_K_M.gguf",
        "sha256": "a4a09d2141717a01b44e7a8dbdb28da8c01e9078c8051367cd6a20f7008ef5a8",
    },
    {
        "repo": "mmnga-o/llm-jp-4-8b-instruct-gguf",
        "revision": "7ae4da12cee2f109509cb8e1d01cf8a0f1a5fbc1",
        "filename": "llm-jp-4-8b-instruct-Q4_K_M.gguf",
        "sha256": "b6a61b9c8d4e7cb1ae543d8fcf472c9fb9abfc5d48af17f5017ce89c2dc0bd56",
    },
]

for item in targets:
    path = Path(
        hf_hub_download(
            repo_id=item["repo"],
            revision=item["revision"],
            filename=item["filename"],
            local_dir="/models",
        )
    )
    h = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != item["sha256"]:
        raise SystemExit(
            f"local_model_sha_mismatch file={item['filename']} expected={item['sha256']} actual={actual}"
        )
    print(f"LOCAL_MODEL_PINNED={path.name} SHA256={actual}")
PY

COPY app.py /app/app.py
COPY runtime /app/runtime
COPY config /app/config
COPY scripts/start_local_cpu.sh /app/scripts/start_local_cpu.sh
RUN chmod +x /app/scripts/start_local_cpu.sh

EXPOSE 7860

CMD ["/app/scripts/start_local_cpu.sh"]
