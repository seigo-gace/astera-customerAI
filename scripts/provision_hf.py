# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface_hub>=1.24.0"]
# ///
from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import HfApi, Volume


ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "")
NAMESPACE = os.environ.get("HF_NAMESPACE", "G-ACE")
SPACE_NAME = os.environ.get("HF_SPACE_NAME", "astera-customerAI")
BUCKET_NAME = os.environ.get("HF_BUCKET_NAME", "astera-customerai-data")
SPACE_ID = f"{NAMESPACE}/{SPACE_NAME}"
BUCKET_ID = f"{NAMESPACE}/{BUCKET_NAME}"
MODEL_REVISION = "cdbee75f17c01a7cc42f958dc650907174af0554"
ASTERA_COMMIT = "67837b0f65ccc42fce5875fc82a1efa3561068ea"


def main() -> None:
    if not TOKEN:
        raise SystemExit("HF_TOKEN is required")
    api = HfApi(token=TOKEN)
    identity = api.whoami()
    print(f"Authenticated as {identity.get('name') or identity.get('fullname')}")

    bucket = api.create_bucket(BUCKET_ID, private=True, exist_ok=True)
    print(f"Private bucket ready: {bucket.bucket_id}")

    volume = Volume(type="bucket", source=BUCKET_ID, mount_path="/data/customer-ai")
    api.create_repo(
        repo_id=SPACE_ID,
        repo_type="space",
        private=True,
        exist_ok=True,
        space_sdk="gradio",
        space_volumes=[volume],
    )
    api.set_space_volumes(SPACE_ID, volumes=[volume])
    print(f"Private Space ready: {SPACE_ID}")

    api.upload_folder(
        repo_id=SPACE_ID,
        repo_type="space",
        folder_path=str(ROOT),
        commit_message=os.environ.get("HF_COMMIT_MESSAGE", "Deploy verified Astera Customer AI runtime"),
        ignore_patterns=[
            ".git/**",
            ".github/**",
            ".pytest_cache/**",
            "__pycache__/**",
            "*.pyc",
            ".env",
            "docs/**",
            "tests/**",
        ],
    )

    variables = {
        "CUSTOMER_AI_DATA_ROOT": "/data/customer-ai",
        "CUSTOMER_AI_MODEL_ID": "Qwen/Qwen3-4B-Instruct-2507",
        "CUSTOMER_AI_MODEL_REVISION": MODEL_REVISION,
        "CUSTOMER_AI_ENABLE_MODEL": "0",
        "CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS": "2100",
        "CUSTOMER_AI_NODE_BINARY": "node",
        "CUSTOMER_AI_NODE_SOCKET": "/tmp/astera-customer-ai-v8.sock",
        "CUSTOMER_AI_NODE_MEMORY_MB": "512",
        "CUSTOMER_AI_ASTERA_REPO": "https://github.com/seigo-gace/astera_v8.git",
        "CUSTOMER_AI_ASTERA_COMMIT": ASTERA_COMMIT,
        "NOTION_DATA_SOURCE_ID": "2a2e5dd3-8492-45b9-a450-b362d02794b4",
        "DEPLOYED_GITHUB_COMMIT": os.environ.get("GITHUB_SHA", "manual"),
    }
    for key, value in variables.items():
        api.add_space_variable(SPACE_ID, key=key, value=value)
    print(f"Configured {len(variables)} non-secret variables")

    zero_requested = False
    try:
        from huggingface_hub import SpaceHardware

        candidates = [str(item.value) for item in SpaceHardware if "zero" in str(item.value).lower()]
        if candidates:
            api.request_space_hardware(SPACE_ID, hardware=candidates[0], sleep_time=1)
            zero_requested = True
            print(f"Requested ZeroGPU hardware: {candidates[0]}")
    except Exception as exc:
        print(f"ZeroGPU hardware must be selected in Space settings: {type(exc).__name__}: {exc}")

    info = api.space_info(SPACE_ID, expand=["runtime", "private", "sha", "sdk", "subdomain"])
    print(
        {
            "space_id": info.id,
            "private": info.private,
            "sdk": info.sdk,
            "sha": info.sha,
            "zero_gpu_requested": zero_requested,
            "runtime": str(info.runtime),
        }
    )
    print("Production secrets remain intentionally unset: CUSTOMER_AI_HMAC_SECRET, GATEWAY_CALLBACK_URL, GATEWAY_CALLBACK_SECRET, NOTION_TOKEN")


if __name__ == "__main__":
    main()
