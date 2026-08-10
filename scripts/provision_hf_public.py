from __future__ import annotations

import io
import json
import os
import secrets
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "").strip()
SPACE_ID = os.environ.get(
    "HF_PUBLIC_SPACE_ID", "G-ACE/astera-customerAI"
).strip()
V3_DATA_SOURCE_ID = "e8f1bcaa-8e1f-482f-97db-f90542699e4a"
ALLOWED_PUBLIC_FILES = {
    ".gitattributes",
    "README.md",
    "bootstrap.py",
    "requirements.txt",
    "runtime.bundle.enc",
}


def runtime_bundle() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path in [ROOT / "public_app.py", ROOT / "runtime", ROOT / "v8", ROOT / "kb"]:
            if not path.exists():
                continue
            if path.is_file():
                archive.add(path, arcname=path.name)
                continue
            for child in path.rglob("*"):
                if not child.is_file() or "__pycache__" in child.parts or child.suffix == ".pyc":
                    continue
                archive.add(child, arcname=str(child.relative_to(ROOT)))
    return stream.getvalue()


def bootstrap_source() -> str:
    return '''from __future__ import annotations
import io, os, sys, tarfile, tempfile
from pathlib import Path
from cryptography.fernet import Fernet
import uvicorn

bundle = Path(__file__).with_name("runtime.bundle.enc").read_bytes()
key = os.environ["CUSTOMER_AI_BUNDLE_KEY"].encode()
plain = Fernet(key).decrypt(bundle)
root = Path(tempfile.mkdtemp(prefix="astera-customer-ai-"))
with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as archive:
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            raise RuntimeError("unsafe_bundle_path")
    archive.extractall(root)
sys.path.insert(0, str(root))
os.chdir(root)
from public_app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")
'''


def read_requirements() -> str:
    base = (ROOT / "requirements.txt").read_text(encoding="utf-8").strip()
    return base + "\ncryptography>=45,<47\n"


def space_info(api: HfApi):
    return api.space_info(
        SPACE_ID,
        expand=["subdomain", "sha", "private", "runtime"],
    )


def space_url(api: HfApi) -> str:
    info = space_info(api)
    subdomain = str(info.subdomain or "").strip()
    if not subdomain:
        raise RuntimeError("CUSTOMER_AI_SPACE_SUBDOMAIN_MISSING")
    return f"https://{subdomain}.hf.space"


def wait_health(url: str, *, private: bool) -> None:
    headers = {"authorization": f"Bearer {TOKEN}"} if private else None
    last = ""
    for attempt in range(30):
        try:
            response = httpx.get(url + "/healthz", headers=headers, timeout=20, follow_redirects=True)
            last = f"{response.status_code}:{response.text[:200]}"
            if response.status_code == 200 and response.json().get("status") == "ok":
                print("HF_CUSTOMER_AI_PRIVATE_HEALTH_OK" if private else "HF_PUBLIC_CUSTOMER_AI_HEALTH_OK")
                return
        except Exception as error:
            last = repr(error)
        if attempt < 29:
            time.sleep(10)
    raise RuntimeError(f"HF_CUSTOMER_AI_HEALTH_TIMEOUT:{last}")


def live_e2e(url: str) -> None:
    suffix = str(int(time.time()))
    payload = {
        "session_id": f"session_deploy_{suffix}",
        "message_id": f"message_deploy_{suffix}",
        "message": "Asteraについて一文で案内してください",
        "locale": "ja-JP",
        "source": "astera-hp",
        "response_mode": "general",
        "mode_source": "selected",
        "current_path": "/ja/",
    }
    last = ""
    for attempt in range(6):
        try:
            response = httpx.post(
                url + "/public/customer-ai/respond",
                json=payload,
                timeout=60,
                follow_redirects=True,
            )
            last = f"{response.status_code}:{response.text[:500]}"
            if response.status_code == 200:
                body = response.json()
                answer = str(body.get("answer") or body.get("clarification") or "").strip()
                if answer:
                    print("HF_PUBLIC_CUSTOMER_AI_E2E_OK")
                    print(
                        "HF_PUBLIC_CUSTOMER_AI_E2E_META="
                        + json.dumps(
                            {
                                "status": body.get("status"),
                                "session_id": body.get("session_id"),
                                "has_answer": True,
                            },
                            ensure_ascii=False,
                        )
                    )
                    return
        except Exception as error:
            last = repr(error)
        if attempt < 5:
            time.sleep(10)
    raise RuntimeError(f"HF_PUBLIC_CUSTOMER_AI_E2E_FAILED:{last}")


def assert_public_repo_sanitized(api: HfApi) -> None:
    files = set(api.list_repo_files(repo_id=SPACE_ID, repo_type="space"))
    unexpected = sorted(files - ALLOWED_PUBLIC_FILES)
    missing = sorted({"README.md", "bootstrap.py", "requirements.txt", "runtime.bundle.enc"} - files)
    if unexpected:
        raise RuntimeError("PUBLIC_SPACE_PLAINTEXT_RESIDUAL:" + ",".join(unexpected[:30]))
    if missing:
        raise RuntimeError("PUBLIC_SPACE_BUNDLE_FILES_MISSING:" + ",".join(missing))
    print("HF_PUBLIC_SPACE_SOURCE_SANITIZED=true")


def main() -> None:
    if not TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")
    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    api = HfApi(token=TOKEN)

    info = space_info(api)
    if not info.private:
        raise RuntimeError("EXISTING_CUSTOMER_AI_SPACE_MUST_START_PRIVATE")
    print("HF_EXISTING_CUSTOMER_AI_SPACE_REUSED=true")

    bundle_key = Fernet.generate_key().decode()
    api.add_space_secret(SPACE_ID, key="CUSTOMER_AI_BUNDLE_KEY", value=bundle_key)
    if notion_token:
        api.add_space_secret(SPACE_ID, key="NOTION_TOKEN", value=notion_token)
        print("HF_PUBLIC_NOTION_SECRET_UPDATED=true")
    else:
        print("HF_PUBLIC_NOTION_SECRET_UPDATED=false")
    api.add_space_secret(
        SPACE_ID,
        key="CUSTOMER_AI_HMAC_SECRET",
        value="base64:" + secrets.token_urlsafe(48),
    )

    variables = {
        "CUSTOMER_AI_DATA_ROOT": "/data/customer-ai",
        "NOTION_DATA_SOURCE_ID": V3_DATA_SOURCE_ID,
        "CUSTOMER_AI_KB_SCHEMA": "v3",
        "CUSTOMER_AI_PUBLIC_ORIGINS": "https://asterav8.jp,https://www.asterav8.jp",
        "CUSTOMER_AI_MODEL_ID": os.environ.get("CUSTOMER_AI_MODEL_ID", "Qwen/Qwen3-0.6B"),
        "CUSTOMER_AI_MODEL_REVISION": os.environ.get(
            "CUSTOMER_AI_MODEL_REVISION", "c1899de289a04d12100db370d81485cdf75e47ca"
        ),
        "CUSTOMER_AI_ENABLE_MODEL": os.environ.get("CUSTOMER_AI_ENABLE_MODEL", "1"),
        "CUSTOMER_AI_NODE_BINARY": "node",
        "CUSTOMER_AI_NODE_SOCKET": "/tmp/customer-ai-v8.sock",
        "CUSTOMER_AI_NODE_MEMORY_MB": "384",
        "CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS": "1800",
        "CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS": "256",
        "CUSTOMER_AI_SESSION_CACHE_MAX_TURNS": "12",
        "CUSTOMER_AI_KB_CACHE_TTL_SECONDS": "120",
        "CUSTOMER_AI_KB_CACHE_MAX_ENTRIES": "512",
        "DEPLOYED_GITHUB_COMMIT": os.environ.get("GITHUB_SHA", "manual"),
    }
    for key, value in variables.items():
        api.add_space_variable(SPACE_ID, key=key, value=value)

    encrypted = Fernet(bundle_key.encode()).encrypt(runtime_bundle())
    with tempfile.TemporaryDirectory(prefix="astera-public-space-") as temporary:
        target = Path(temporary)
        (target / "bootstrap.py").write_text(bootstrap_source(), encoding="utf-8")
        (target / "requirements.txt").write_text(read_requirements(), encoding="utf-8")
        (target / "runtime.bundle.enc").write_bytes(encrypted)
        (target / "README.md").write_text(
            "---\n"
            "title: Astera Customer AI Public API\n"
            "emoji: ✨\n"
            "colorFrom: gray\n"
            "colorTo: blue\n"
            "sdk: gradio\n"
            "sdk_version: 6.5.1\n"
            "app_file: bootstrap.py\n"
            "pinned: false\n"
            "---\n\n"
            "# Astera Customer AI Public API\n\n"
            "Public synchronous API surface for the Astera official website. "
            "Runtime source is deployed as an encrypted bundle from the private GitHub repository.\n",
            encoding="utf-8",
        )
        api.upload_folder(
            repo_id=SPACE_ID,
            repo_type="space",
            folder_path=str(target),
            delete_patterns=["*", "**/*"],
            commit_message=f"Deploy encrypted public Customer AI {os.environ.get('GITHUB_SHA', 'manual')}",
        )

    assert_public_repo_sanitized(api)
    url = space_url(api)
    wait_health(url, private=True)

    # Publish only after source sanitization and private-runtime health both pass.
    api.update_repo_settings(repo_id=SPACE_ID, repo_type="space", private=False)
    if space_info(api).private:
        raise RuntimeError("CUSTOMER_AI_SPACE_PUBLICATION_FAILED")
    print("HF_CUSTOMER_AI_SPACE_PUBLIC=true")
    wait_health(url, private=False)
    live_e2e(url)


if __name__ == "__main__":
    main()
