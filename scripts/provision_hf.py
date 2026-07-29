# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface_hub>=1.24.0", "httpx>=0.28,<1"]
# ///
from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

import httpx
from huggingface_hub import HfApi, Volume

# Deployment preserves existing HF runtime secrets when GitHub values are absent.
ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "")
NAMESPACE = os.environ.get("HF_NAMESPACE", "G-ACE")
SPACE_NAME = os.environ.get("HF_SPACE_NAME", "astera-customerAI")
BUCKET_NAME = os.environ.get("HF_BUCKET_NAME", "astera-customerai-data")
SPACE_ID = f"{NAMESPACE}/{SPACE_NAME}"
BUCKET_ID = f"{NAMESPACE}/{BUCKET_NAME}"
MODEL_REVISION = os.environ.get(
    "CUSTOMER_AI_MODEL_REVISION",
    "c1899de289a04d12100db370d81485cdf75e47ca",
)

RUNTIME_SECRETS = (
    "CUSTOMER_AI_HMAC_SECRET",
    "INTERNAL_EVENT_API_URL",
    "INTERNAL_EVENT_API_TOKEN",
    "NOTION_TOKEN",
    "NOTION_DATA_SOURCE_ID",
    "NOTION_INDEX_DATA_SOURCE_ID",
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

    if "CUSTOMER_AI_HMAC_SECRET" not in values:
        generated = base64.b64encode(secrets.token_bytes(48)).decode("ascii")
        values["CUSTOMER_AI_HMAC_SECRET"] = "base64:" + generated
        print("HF_RUNTIME_HMAC_SECRET_BOOTSTRAPPED=true")
        missing = [name for name in missing if name != "CUSTOMER_AI_HMAC_SECRET"]

    if missing:
        # Upload and Space configuration do not delete existing HF secrets.
        print("HF_RUNTIME_SECRETS_PRESERVED=" + ",".join(missing))
    return values


def inspect_persisted_snapshot(api: HfApi) -> None:
    with tempfile.TemporaryDirectory(prefix="hf-kb-manifest-") as temporary:
        local = Path(temporary) / "manifest.json"
        api.download_bucket_files(
            BUCKET_ID,
            files=[("kb/manifest.json", local)],
            raise_on_missing_files=False,
        )
        if not local.exists():
            print("HF_PERSISTED_KB_MANIFEST=missing")
            return
        try:
            manifest = json.loads(local.read_text(encoding="utf-8"))
        except Exception as error:
            print(f"HF_PERSISTED_KB_MANIFEST=invalid:{type(error).__name__}")
            return
        safe = {
            "version": manifest.get("version"),
            "page_count": manifest.get("page_count"),
            "index_count": manifest.get("index_count"),
            "schema_version": manifest.get("schema_version"),
        }
        print("HF_PERSISTED_KB_MANIFEST=" + json.dumps(safe, sort_keys=True))


def verify_bucket(api: HfApi) -> None:
    payload = json.dumps(
        {
            "github_sha": os.environ.get("GITHUB_SHA", "manual"),
            "verified_at": time.time(),
        },
        sort_keys=True,
    ).encode("utf-8")
    probe_path = (
        "runtime-verification/deploy-"
        f"{os.environ.get('GITHUB_RUN_ID', 'manual')}.json"
    )
    with tempfile.TemporaryDirectory(prefix="hf-customer-ai-") as temporary:
        downloaded = Path(temporary) / "probe.json"
        try:
            api.batch_bucket_files(BUCKET_ID, add=[(payload, probe_path)])
            api.download_bucket_files(
                BUCKET_ID,
                files=[(probe_path, str(downloaded))],
            )
            if downloaded.read_bytes() != payload:
                raise RuntimeError("HF_BUCKET_READ_AFTER_WRITE_MISMATCH")
            print("HF_BUCKET_WRITE_READ_OK")
        finally:
            api.batch_bucket_files(BUCKET_ID, delete=[probe_path])
            print("HF_BUCKET_PROBE_REMOVED")


def dump_space_logs(api: HfApi) -> None:
    for label, build in (("BUILD", True), ("RUN", False)):
        print(f"HF_SPACE_{label}_LOG_BEGIN")
        try:
            for line in api.fetch_space_logs(SPACE_ID, build=build, follow=False):
                print(line, end="" if line.endswith("\n") else "\n")
        except Exception as error:
            print(f"HF_SPACE_{label}_LOG_ERROR={error!r}")
        print(f"HF_SPACE_{label}_LOG_END")


def wait_for_space_health(api: HfApi) -> None:
    last = ""
    last_stage = ""
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for attempt in range(40):
        runtime = api.get_space_runtime(SPACE_ID)
        stage = str(runtime.stage)
        stage_upper = stage.upper()
        if stage != last_stage:
            print(f"HF_SPACE_STAGE={stage}")
            last_stage = stage
        if "ERROR" in stage_upper:
            dump_space_logs(api)
            raise RuntimeError(f"HF_SPACE_TERMINAL_STAGE:{stage}")

        if stage_upper != "RUNNING":
            if attempt == 39:
                break
            time.sleep(20)
            continue

        info = api.space_info(
            SPACE_ID,
            expand=["runtime", "private", "sha", "sdk", "subdomain"],
        )
        if not info.private:
            raise RuntimeError("HF_SPACE_NOT_PRIVATE")
        if info.subdomain:
            health_url = f"https://{info.subdomain}.hf.space/healthz"
            try:
                response = httpx.get(
                    health_url,
                    headers=headers,
                    timeout=30,
                    follow_redirects=True,
                )
                last = f"{response.status_code}:{response.text[:300]}"
                if (
                    response.status_code == 200
                    and response.json().get("status") == "ok"
                ):
                    print("HF_SPACE_HEALTH_OK")
                    print("HF_PRIVATE_RUNTIME_STARTED")
                    return
            except Exception as error:  # cold-start transition
                last = repr(error)
        if attempt == 39:
            break
        time.sleep(20)
    dump_space_logs(api)
    raise RuntimeError(f"HF_SPACE_HEALTH_TIMEOUT:{last or last_stage}")


def main() -> None:
    runtime_secrets = runtime_environment()
    api = HfApi(token=TOKEN)
    identity = api.whoami()
    print(
        "HF_AUTHENTICATED_AS="
        f"{identity.get('name') or identity.get('fullname')}"
    )

    bucket = api.create_bucket(BUCKET_ID, private=True, exist_ok=True)
    if not bucket:
        raise RuntimeError("HF_BUCKET_CREATE_FAILED")
    print(f"HF_PRIVATE_BUCKET_READY={BUCKET_ID}")
    inspect_persisted_snapshot(api)

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
    api.update_repo_settings(
        repo_id=SPACE_ID,
        repo_type="space",
        private=True,
    )
    api.set_space_volumes(SPACE_ID, volumes=[volume])
    print(f"HF_PRIVATE_SPACE_READY={SPACE_ID}")
    print("HF_BUCKET_MOUNT=/data/customer-ai")

    for key, value in runtime_secrets.items():
        api.add_space_secret(SPACE_ID, key=key, value=value)

    variables = {
        "CUSTOMER_AI_DATA_ROOT": "/data/customer-ai",
        "CUSTOMER_AI_MODEL_ID": os.environ.get(
            "CUSTOMER_AI_MODEL_ID",
            "Qwen/Qwen3-0.6B",
        ),
        "CUSTOMER_AI_MODEL_REVISION": MODEL_REVISION,
        "CUSTOMER_AI_ENABLE_MODEL": os.environ.get(
            "CUSTOMER_AI_ENABLE_MODEL",
            "1",
        ),
        "CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS": "3600",
        "CUSTOMER_AI_NODE_BINARY": "node",
        "CUSTOMER_AI_NODE_SOCKET": "/tmp/customer-ai-v8.sock",
        "CUSTOMER_AI_NODE_MEMORY_MB": "384",
        "CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS": "1800",
        "CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS": "256",
        "CUSTOMER_AI_SESSION_CACHE_MAX_TURNS": "12",
        "CUSTOMER_AI_KB_CACHE_TTL_SECONDS": "120",
        "CUSTOMER_AI_KB_CACHE_MAX_ENTRIES": "512",
        "INTERNAL_EVENT_SOURCE_ID": os.environ.get(
            "INTERNAL_EVENT_SOURCE_ID",
            "hf-private-runtime",
        ),
        "INTERNAL_EVENT_RESULT_DESTINATION_ID": os.environ.get(
            "INTERNAL_EVENT_RESULT_DESTINATION_ID",
            "app-receiver",
        ),
        "DEPLOYED_GITHUB_COMMIT": os.environ.get("GITHUB_SHA", "manual"),
    }
    for key, value in variables.items():
        api.add_space_variable(SPACE_ID, key=key, value=value)
    print(f"HF_SPACE_SECRETS_CONFIGURED={len(runtime_secrets)}")
    print(f"HF_SPACE_VARIABLES_CONFIGURED={len(variables)}")
    print(f"HF_FREE_LOCAL_MODEL={variables['CUSTOMER_AI_MODEL_ID']}")
    print(f"HF_FREE_LOCAL_MODEL_REVISION={MODEL_REVISION}")

    api.upload_folder(
        repo_id=SPACE_ID,
        repo_type="space",
        folder_path=str(ROOT),
        commit_message=os.environ.get(
            "HF_COMMIT_MESSAGE",
            "Deploy Customer AI from GitHub main",
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
            "test-results/**",
            "state.md",
        ],
    )
    api.restart_space(SPACE_ID)
    print("HF_SPACE_UPLOAD_COMPLETE")

    verify_bucket(api)
    wait_for_space_health(api)


if __name__ == "__main__":
    main()
