# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface_hub>=1.24.0", "httpx>=0.28,<1"]
# ///
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import httpx
from huggingface_hub import HfApi, Volume

ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "")
NAMESPACE = os.environ.get("HF_NAMESPACE", "G-ACE")
SPACE_NAME = os.environ.get("HF_SPACE_NAME", "astera-customerAI")
BUCKET_NAME = os.environ.get("HF_BUCKET_NAME", "astera-customerai-data")
SPACE_ID = f"{NAMESPACE}/{SPACE_NAME}"
BUCKET_ID = f"{NAMESPACE}/{BUCKET_NAME}"
MODEL_REVISION = os.environ.get(
    "CUSTOMER_AI_MODEL_REVISION",
    "cdbee75f17c01a7cc42f958dc650907174af0554",
)

RUNTIME_SECRETS = (
    "CUSTOMER_AI_HMAC_SECRET",
    "INTERNAL_EVENT_API_URL",
    "INTERNAL_EVENT_API_TOKEN",
    "NOTION_TOKEN",
    "NOTION_DATA_SOURCE_ID",
)


def runtime_environment() -> dict[str, str]:
    if not TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")

    values: dict[str, str] = {}
    missing: list[str] = []
    for name in RUNTIME_SECRETS:
        value = os.environ.get(name, "").strip()
        if value:
            values[name] = value
        else:
            missing.append(name)

    if missing:
        # upload_folder and Space configuration do not delete existing HF secrets.
        # Missing GitHub secrets are therefore left untouched so an existing Space
        # can retain its current runtime credentials during a code-only deployment.
        print("HF_RUNTIME_SECRETS_PRESERVED=" + ",".join(missing))
    return values


def verify_bucket(api: HfApi) -> None:
    payload = json.dumps(
        {
            "github_sha": os.environ.get("GITHUB_SHA", "manual"),
            "verified_at": time.time(),
        },
        sort_keys=True,
    ).encode("utf-8")
    probe_path = f"runtime-verification/deploy-{os.environ.get('GITHUB_RUN_ID', 'manual')}.json"
    with tempfile.TemporaryDirectory(prefix="hf-customer-ai-") as temporary:
        downloaded = Path(temporary) / "probe.json"
        try:
            api.batch_bucket_files(BUCKET_ID, add=[(payload, probe_path)])
            api.download_bucket_files(BUCKET_ID, files=[(probe_path, str(downloaded))])
            if downloaded.read_bytes() != payload:
                raise RuntimeError("HF_BUCKET_READ_AFTER_WRITE_MISMATCH")
            print("HF_BUCKET_WRITE_READ_OK")
        finally:
            api.batch_bucket_files(BUCKET_ID, delete=[probe_path])
            print("HF_BUCKET_PROBE_REMOVED")


def wait_for_space(api: HfApi) -> None:
    last = ""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for attempt in range(30):
        info = api.space_info(
            SPACE_ID,
            expand=["runtime", "private", "sha", "sdk", "subdomain"],
        )
        if not info.private:
            raise RuntimeError("HF_SPACE_NOT_PRIVATE")
        if info.subdomain:
            health_url = f"https://{info.subdomain}.hf.space/healthz"
            ready_url = f"https://{info.subdomain}.hf.space/readyz"
            try:
                response = httpx.get(
                    health_url,
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                )
                last = f"{response.status_code}:{response.text[:300]}"
                if response.status_code == 200 and response.json().get("status") == "ok":
                    print("HF_SPACE_HEALTH_OK")
                    ready = httpx.get(
                        ready_url,
                        headers=headers,
                        timeout=30,
                        follow_redirects=True,
                    )
                    print(f"HF_SPACE_READY_STATUS={ready.status_code}")
                    print(ready.text[:2000])
                    print("HF_PRIVATE_RUNTIME_DEPLOYED")
                    return
            except Exception as error:  # build and cold-start transition
                last = repr(error)
        if attempt == 29:
            break
        time.sleep(20)
    raise RuntimeError(f"HF_SPACE_HEALTH_TIMEOUT:{last}")


def main() -> None:
    runtime_secrets = runtime_environment()
    api = HfApi(token=TOKEN)
    identity = api.whoami()
    print(f"HF_AUTHENTICATED_AS={identity.get('name') or identity.get('fullname')}")

    bucket = api.create_bucket(BUCKET_ID, private=True, exist_ok=True)
    if not bucket:
        raise RuntimeError("HF_BUCKET_CREATE_FAILED")
    print(f"HF_PRIVATE_BUCKET_READY={BUCKET_ID}")

    volume = Volume(
        type="bucket",
        source=BUCKET_ID,
        mount_path="/data/customer-ai",
        read_only=False,
    )
    api.create_repo(
        repo_id=SPACE_ID,
        repo_type="space",
        private=True,
        exist_ok=True,
        space_sdk="gradio",
        space_volumes=[volume],
    )
    api.update_repo_settings(repo_id=SPACE_ID, repo_type="space", private=True)
    api.set_space_volumes(SPACE_ID, volumes=[volume])
    print(f"HF_PRIVATE_SPACE_READY={SPACE_ID}")
    print("HF_BUCKET_MOUNT=/data/customer-ai")

    for key, value in runtime_secrets.items():
        api.add_space_secret(SPACE_ID, key=key, value=value)

    variables = {
        "CUSTOMER_AI_DATA_ROOT": "/data/customer-ai",
        "CUSTOMER_AI_MODEL_ID": os.environ.get(
            "CUSTOMER_AI_MODEL_ID", "Qwen/Qwen3-4B-Instruct-2507"
        ),
        "CUSTOMER_AI_MODEL_REVISION": MODEL_REVISION,
        "CUSTOMER_AI_ENABLE_MODEL": os.environ.get("CUSTOMER_AI_ENABLE_MODEL", "0"),
        "CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS": "2100",
        "CUSTOMER_AI_NODE_BINARY": "node",
        "CUSTOMER_AI_NODE_SOCKET": "/tmp/customer-ai-v8.sock",
        "CUSTOMER_AI_NODE_MEMORY_MB": "384",
        "CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS": "1800",
        "CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS": "256",
        "CUSTOMER_AI_SESSION_CACHE_MAX_TURNS": "12",
        "CUSTOMER_AI_KB_CACHE_TTL_SECONDS": "120",
        "CUSTOMER_AI_KB_CACHE_MAX_ENTRIES": "256",
        "INTERNAL_EVENT_SOURCE_ID": os.environ.get(
            "INTERNAL_EVENT_SOURCE_ID", "hf-private-runtime"
        ),
        "INTERNAL_EVENT_RESULT_DESTINATION_ID": os.environ.get(
            "INTERNAL_EVENT_RESULT_DESTINATION_ID", "app-receiver"
        ),
        "DEPLOYED_GITHUB_COMMIT": os.environ.get("GITHUB_SHA", "manual"),
    }
    for key, value in variables.items():
        api.add_space_variable(SPACE_ID, key=key, value=value)
    print(f"HF_SPACE_SECRETS_CONFIGURED={len(runtime_secrets)}")
    print(f"HF_SPACE_VARIABLES_CONFIGURED={len(variables)}")

    api.upload_folder(
        repo_id=SPACE_ID,
        repo_type="space",
        folder_path=str(ROOT),
        commit_message=os.environ.get(
            "HF_COMMIT_MESSAGE", "Deploy Customer AI from GitHub main"
        ),
        ignore_patterns=[
            ".git/**",
            ".github/**",
            ".pytest_cache/**",
            ".pytest-*/**",
            ".ruff_cache/**",
            "__pycache__/**",
            "*.pyc",
            ".env",
            "docs/**",
            "tests/**",
            "edge/**",
            "test-results/**",
            "state.md",
        ],
    )
    api.restart_space(SPACE_ID)
    print("HF_SPACE_UPLOAD_COMPLETE")

    verify_bucket(api)
    wait_for_space(api)


if __name__ == "__main__":
    main()
