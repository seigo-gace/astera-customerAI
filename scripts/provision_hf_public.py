from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from huggingface_hub import HfApi
from huggingface_hub.errors import RepositoryNotFoundError


ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "").strip()
SPACE_ID = os.environ.get(
    "HF_PUBLIC_SPACE_ID", "G-ACE/astera-customerAI-public"
).strip()
V3_DATA_SOURCE_ID = "e8f1bcaa-8e1f-482f-97db-f90542699e4a"
BUNDLED_NOTION_TOKEN = "bundled:hp-public-v2"
ALLOWED_PUBLIC_FILES = {
    ".gitattributes",
    "Dockerfile",
    "README.md",
    "bootstrap.py",
    "requirements.txt",
    "runtime.bundle.enc",
}


def runtime_bundle() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for path in [
            ROOT / "public_app.py",
            ROOT / "runtime",
            ROOT / "v8",
            ROOT / "kb",
        ]:
            if not path.exists():
                continue
            if path.is_file():
                archive.add(path, arcname=path.name)
                continue
            for child in path.rglob("*"):
                if (
                    not child.is_file()
                    or "__pycache__" in child.parts
                    or child.suffix == ".pyc"
                ):
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


def dockerfile_source() -> str:
    return '''FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \\
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \\
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bootstrap.py runtime.bundle.enc /app/

RUN groupadd --system astera \\
    && useradd --system --gid astera --home-dir /app --shell /usr/sbin/nologin astera \\
    && chown -R astera:astera /app

USER astera
EXPOSE 7860
CMD ["python", "bootstrap.py"]
'''


def read_requirements() -> str:
    lines = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("gradio"):
            continue
        lines.append(line)
    lines.append("cryptography>=45,<47")
    return "\n".join(lines) + "\n"


def space_info(api: HfApi):
    return api.space_info(
        SPACE_ID,
        expand=["subdomain", "sha", "private", "runtime"],
    )


def ensure_space(api: HfApi):
    try:
        return space_info(api), False
    except RepositoryNotFoundError:
        api.create_repo(
            repo_id=SPACE_ID,
            repo_type="space",
            private=True,
            exist_ok=True,
            space_sdk="docker",
        )
        return space_info(api), True


def space_url(api: HfApi) -> str:
    info = space_info(api)
    subdomain = str(info.subdomain or "").strip()
    if not subdomain:
        raise RuntimeError("CUSTOMER_AI_SPACE_SUBDOMAIN_MISSING")
    return f"https://{subdomain}.hf.space"


def auth_headers(private: bool) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"} if private else {}


def wait_ready(api: HfApi, url: str, *, private: bool) -> None:
    last = ""
    for attempt in range(36):
        try:
            response = httpx.get(
                url + "/readyz",
                headers=auth_headers(private),
                timeout=20,
                follow_redirects=True,
            )
            last = f"{response.status_code}:{response.text[:500]}"
            if response.status_code == 200 and response.json().get("ready") is True:
                print("HF_CUSTOMER_AI_READY=true")
                return
        except Exception as error:
            last = repr(error)
        if attempt < 35:
            time.sleep(10)
    runtime = ""
    try:
        runtime = repr(space_info(api).runtime)
    except Exception as error:
        runtime = f"space_info_failed:{error!r}"
    raise RuntimeError(f"HF_CUSTOMER_AI_READY_TIMEOUT:{last}:runtime={runtime[:1000]}")


def live_e2e(url: str) -> None:
    suffix = str(int(time.time()))
    session_id = f"session_deploy_{suffix}"
    payload = {
        "session_id": session_id,
        "message_id": f"message_deploy_{suffix}",
        "message": "料金を教えて",
        "locale": "ja-JP",
        "source": "astera-hp",
        "response_mode": "billing",
        "mode_source": "selected",
        "current_path": "/ja/",
    }
    headers = {"origin": "https://asterav8.jp", "content-type": "application/json"}
    last = ""
    for attempt in range(6):
        try:
            response = httpx.post(
                url + "/respond",
                json=payload,
                headers=headers,
                timeout=60,
                follow_redirects=True,
            )
            last = f"{response.status_code}:{response.text[:800]}"
            if response.status_code == 200:
                body = response.json()
                answer = str(body.get("answer") or body.get("clarification") or "").strip()
                if answer:
                    deleted = httpx.delete(
                        url + "/sessions/" + session_id,
                        headers={"origin": "https://asterav8.jp"},
                        timeout=20,
                        follow_redirects=True,
                    )
                    if deleted.status_code != 200 or deleted.json().get("ok") is not True:
                        raise RuntimeError(f"session_delete_failed:{deleted.status_code}:{deleted.text[:300]}")
                    print("HF_PUBLIC_CUSTOMER_AI_E2E_OK")
                    print(
                        "HF_PUBLIC_CUSTOMER_AI_E2E_META="
                        + json.dumps(
                            {
                                "status": body.get("status"),
                                "session_id": body.get("session_id"),
                                "has_answer": True,
                                "session_deleted": True,
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
    missing = sorted(
        {"Dockerfile", "README.md", "bootstrap.py", "requirements.txt", "runtime.bundle.enc"}
        - files
    )
    if unexpected:
        raise RuntimeError("PUBLIC_SPACE_PLAINTEXT_RESIDUAL:" + ",".join(unexpected[:30]))
    if missing:
        raise RuntimeError("PUBLIC_SPACE_BUNDLE_FILES_MISSING:" + ",".join(missing))
    print("HF_PUBLIC_SPACE_SOURCE_SANITIZED=true")


def main() -> None:
    if not TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    runtime_notion_token = notion_token or BUNDLED_NOTION_TOKEN
    api = HfApi(token=TOKEN)
    info, created = ensure_space(api)
    print(f"HF_PUBLIC_SPACE_CREATED={str(created).lower()}")
    print(f"HF_PUBLIC_SPACE_START_PRIVATE={str(bool(info.private)).lower()}")

    bundle_key = Fernet.generate_key().decode()
    api.add_space_secret(SPACE_ID, key="CUSTOMER_AI_BUNDLE_KEY", value=bundle_key)
    api.add_space_secret(SPACE_ID, key="HF_TOKEN", value=TOKEN)
    api.add_space_secret(SPACE_ID, key="NOTION_TOKEN", value=runtime_notion_token)
    print(f"HF_PUBLIC_NOTION_SOURCE={'live' if notion_token else 'bundled'}")

    variables = {
        "CUSTOMER_AI_DATA_ROOT": "/tmp/customer-ai",
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
        (target / "Dockerfile").write_text(dockerfile_source(), encoding="utf-8")
        (target / "requirements.txt").write_text(read_requirements(), encoding="utf-8")
        (target / "runtime.bundle.enc").write_bytes(encrypted)
        (target / "README.md").write_text(
            "---\n"
            "title: Astera Customer AI Public API\n"
            "emoji: ✨\n"
            "colorFrom: gray\n"
            "colorTo: blue\n"
            "sdk: docker\n"
            "app_port: 7860\n"
            "pinned: false\n"
            "---\n\n"
            "# Astera Customer AI Public API\n\n"
            "Thin synchronous API boundary for the Astera official website. "
            "The private runtime is deployed only as an encrypted bundle.\n",
            encoding="utf-8",
        )
        api.upload_folder(
            repo_id=SPACE_ID,
            repo_type="space",
            folder_path=str(target),
            delete_patterns=["*", "**/*"],
            commit_message=(
                "Deploy encrypted Customer AI boundary "
                f"{os.environ.get('GITHUB_SHA', 'manual')}"
            ),
        )

    assert_public_repo_sanitized(api)
    url = space_url(api)
    private = bool(space_info(api).private)
    wait_ready(api, url, private=private)

    if private:
        api.update_repo_settings(repo_id=SPACE_ID, repo_type="space", private=False)
        if space_info(api).private:
            raise RuntimeError("CUSTOMER_AI_SPACE_PUBLICATION_FAILED")
        print("HF_CUSTOMER_AI_SPACE_PUBLIC=true")
        wait_ready(api, url, private=False)

    live_e2e(url)
    print(f"HF_PUBLIC_CUSTOMER_AI_URL={url}")


if __name__ == "__main__":
    main()
