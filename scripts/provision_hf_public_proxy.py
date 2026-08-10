from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import httpx
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
TOKEN = os.environ.get("HF_TOKEN", "").strip()
HMAC_SECRET = os.environ.get("CUSTOMER_AI_HMAC_SECRET", "").strip()
PRIVATE_SPACE_ID = os.environ.get("HF_PRIVATE_SPACE_ID", "G-ACE/astera-customerAI").strip()
PUBLIC_SPACE_ID = os.environ.get("HF_PUBLIC_SPACE_ID", "G-ACE/astera-customerAI-public").strip()


def main() -> None:
    if not TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")
    if not HMAC_SECRET:
        raise SystemExit("CUSTOMER_AI_HMAC_SECRET_MISSING")

    api = HfApi(token=TOKEN)
    private = api.space_info(PRIVATE_SPACE_ID, expand=["subdomain", "private", "runtime", "sha"])
    if not private.private:
        raise RuntimeError("PRIVATE_RUNTIME_IS_NOT_PRIVATE")
    if not private.subdomain:
        raise RuntimeError("PRIVATE_RUNTIME_SUBDOMAIN_MISSING")
    private_url = f"https://{private.subdomain}.hf.space"

    api.create_repo(repo_id=PUBLIC_SPACE_ID, repo_type="space", private=False, exist_ok=True, space_sdk="gradio")
    api.update_repo_settings(repo_id=PUBLIC_SPACE_ID, repo_type="space", private=False)
    api.add_space_secret(PUBLIC_SPACE_ID, key="HF_TOKEN", value=TOKEN)
    api.add_space_secret(PUBLIC_SPACE_ID, key="CUSTOMER_AI_HMAC_SECRET", value=HMAC_SECRET)
    api.add_space_secret(PUBLIC_SPACE_ID, key="PRIVATE_HF_RUNTIME_URL", value=private_url)
    api.add_space_variable(PUBLIC_SPACE_ID, key="CUSTOMER_AI_PUBLIC_ORIGINS", value="https://asterav8.jp,https://www.asterav8.jp")
    api.add_space_variable(PUBLIC_SPACE_ID, key="CUSTOMER_AI_PUBLIC_SESSION_REQUESTS_PER_MINUTE", value="30")
    api.add_space_variable(PUBLIC_SPACE_ID, key="DEPLOYED_GITHUB_COMMIT", value=os.environ.get("GITHUB_SHA", "manual"))

    requirements = "fastapi==0.128.2\nuvicorn==0.48.0\nhttpx>=0.28,<1\n"
    readme = "---\ntitle: Astera Customer AI Public Facade\nemoji: ✨\ncolorFrom: gray\ncolorTo: blue\nsdk: gradio\nsdk_version: 6.5.1\napp_file: app.py\npinned: false\n---\n\n# Astera Customer AI Public Facade\n\nPublic facade deployed from the private GitHub repository. It forwards signed server-to-server requests to the private Hugging Face runtime. Private runtime URL and credentials are stored only as Space secrets.\n"

    with tempfile.TemporaryDirectory(prefix="astera-customer-ai-facade-") as temporary:
        target = Path(temporary)
        target.joinpath("app.py").write_text(ROOT.joinpath("public_proxy.py").read_text(encoding="utf-8"), encoding="utf-8")
        target.joinpath("requirements.txt").write_text(requirements, encoding="utf-8")
        target.joinpath("README.md").write_text(readme, encoding="utf-8")
        api.upload_folder(repo_id=PUBLIC_SPACE_ID, repo_type="space", folder_path=str(target), commit_message=f"Deploy Customer AI facade {os.environ.get('GITHUB_SHA', 'manual')}")

    api.restart_space(PUBLIC_SPACE_ID)
    info = api.space_info(PUBLIC_SPACE_ID, expand=["subdomain", "private", "runtime", "sha"])
    if info.private:
        raise RuntimeError("PUBLIC_FACADE_IS_PRIVATE")
    if not info.subdomain:
        raise RuntimeError("PUBLIC_FACADE_SUBDOMAIN_MISSING")
    public_url = f"https://{info.subdomain}.hf.space"
    print(f"HF_PUBLIC_CUSTOMER_AI_FACADE_URL={public_url}")

    last = ""
    for attempt in range(45):
        try:
            response = httpx.get(public_url + "/healthz", timeout=20, follow_redirects=True)
            last = f"{response.status_code}:{response.text[:300]}"
            if response.status_code == 200:
                body = response.json()
                if body.get("status") == "ok" and body.get("runtime_configured") is True:
                    print("HF_PUBLIC_FACADE_HEALTH_OK")
                    break
        except Exception as error:
            last = repr(error)
        if attempt == 44:
            raise RuntimeError(f"HF_PUBLIC_FACADE_HEALTH_TIMEOUT:{last}")
        time.sleep(15)

    suffix = str(int(time.time()))
    payload = {
        "message": "Asteraについて一文で案内してください",
        "source": "astera-hp",
        "locale": "ja-JP",
        "session_id": f"session_deploy_{suffix}",
        "message_id": f"message_deploy_{suffix}",
        "response_mode": "general",
        "mode_source": "selected",
        "current_path": "/ja/",
    }
    response = httpx.post(public_url + "/public/customer-ai/respond", json=payload, timeout=60, follow_redirects=True)
    print(f"HF_PUBLIC_FACADE_E2E_STATUS={response.status_code}")
    if response.status_code != 200:
        raise RuntimeError(f"HF_PUBLIC_FACADE_E2E_FAILED:{response.status_code}:{response.text[:500]}")
    body = response.json()
    if not str(body.get("answer") or body.get("clarification") or "").strip():
        raise RuntimeError(f"HF_PUBLIC_FACADE_EMPTY_ANSWER:{body}")
    print("HF_PUBLIC_FACADE_E2E_OK")


if __name__ == "__main__":
    main()
