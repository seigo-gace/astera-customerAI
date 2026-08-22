from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from huggingface_hub import HfApi, HfFileSystem, Volume, bucket_info

from runtime.kb_bucket import (
    HF_KB_ACTIVE_POINTER_DEFAULT,
    HF_KB_BUCKET_DEFAULT,
    HF_KB_MOUNT_DEFAULT,
)

SPACE_ID_DEFAULT = "G-ACE/astera-customerAI"
SPACE_URL_DEFAULT = "https://g-ace-astera-customerai.hf.space"
REQUIRED_REPO_FILES = (
    "app.py",
    "Dockerfile",
    "requirements.txt",
    "pyproject.toml",
    ".dockerignore",
)
REQUIRED_REPO_DIRS = ("config", "runtime")


def _git_head(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _validate_repo(root: Path) -> None:
    missing = [name for name in REQUIRED_REPO_FILES if not (root / name).is_file()]
    missing.extend(name for name in REQUIRED_REPO_DIRS if not (root / name).is_dir())
    if missing:
        raise SystemExit(f"repository_payload_missing={missing}")


def _required_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"{key}_missing")
    return value


def _bucket_config() -> dict[str, str]:
    return {
        "bucket_id": os.environ.get("CUSTOMER_AI_KB_BUCKET_ID", "").strip() or HF_KB_BUCKET_DEFAULT,
        "build_id": _required_env("CUSTOMER_AI_KB_BUILD_ID"),
        "mount_path": os.environ.get("CUSTOMER_AI_KB_MOUNT_PATH", "").strip() or HF_KB_MOUNT_DEFAULT,
        "active_pointer": (
            os.environ.get("CUSTOMER_AI_KB_ACTIVE_POINTER", "").strip()
            or HF_KB_ACTIVE_POINTER_DEFAULT
        ),
    }


def _space_readme(root: Path) -> str:
    body = (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").is_file() else ""
    return (
        "---\n"
        "title: Astera Customer AI\n"
        "sdk: docker\n"
        "app_port: 7860\n"
        "---\n\n"
        + body
    )


def _stage_payload(root: Path, stage: Path, bucket: dict[str, str]) -> dict[str, Any]:
    for name in REQUIRED_REPO_FILES:
        shutil.copy2(root / name, stage / name)
    for name in REQUIRED_REPO_DIRS:
        shutil.copytree(root / name, stage / name)
    (stage / "README.md").write_text(_space_readme(root), encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": 4,
        "source_sha": _git_head(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_storage": "huggingface_storage_bucket_volume",
        "kb_bucket_id": bucket["bucket_id"],
        "kb_build_id": bucket["build_id"],
        "kb_mount_path": bucket["mount_path"],
        "kb_active_pointer": bucket["active_pointer"],
        "kb_embedded_in_space": False,
        "deployment_path": "direct_hf_hub_no_github_actions",
    }
    (stage / "deployment-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _expected_files(stage: Path) -> set[str]:
    return {
        str(path.relative_to(stage)).replace("\\", "/")
        for path in stage.rglob("*")
        if path.is_file()
    }


def _read_active_pointer(*, token: str, bucket: dict[str, str]) -> dict[str, object]:
    fs = HfFileSystem(token=token)
    uri = f"hf://buckets/{bucket['bucket_id']}/{bucket['active_pointer']}"
    try:
        with fs.open(uri, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        raise SystemExit(f"kb_active_pointer_read_failed={type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("kb_active_pointer_invalid")
    if str(payload.get("build_id") or "") != bucket["build_id"]:
        raise SystemExit(
            f"kb_active_build_id_mismatch expected={bucket['build_id']!r} "
            f"actual={payload.get('build_id')!r}"
        )
    canonical_path = str(payload.get("canonical_path") or "").strip()
    if not canonical_path:
        raise SystemExit("kb_active_canonical_path_missing")
    canonical_uri = f"hf://buckets/{bucket['bucket_id']}/{canonical_path}"
    if not fs.exists(canonical_uri):
        raise SystemExit("kb_active_canonical_remote_missing")
    return payload


def _mount_kb_bucket(api: HfApi, *, space_id: str, bucket: dict[str, str]) -> None:
    runtime = api.get_space_runtime(repo_id=space_id)
    existing = list(getattr(runtime, "volumes", None) or [])
    retained = [
        volume
        for volume in existing
        if getattr(volume, "mount_path", None) != bucket["mount_path"]
        and not (
            getattr(volume, "type", None) == "bucket"
            and getattr(volume, "source", None) == bucket["bucket_id"]
        )
    ]
    retained.append(
        Volume(
            type="bucket",
            source=bucket["bucket_id"],
            mount_path=bucket["mount_path"],
            read_only=True,
        )
    )
    api.set_space_volumes(repo_id=space_id, volumes=retained)

    readback = api.get_space_runtime(repo_id=space_id)
    volumes = list(getattr(readback, "volumes", None) or [])
    matched = [
        volume
        for volume in volumes
        if getattr(volume, "type", None) == "bucket"
        and getattr(volume, "source", None) == bucket["bucket_id"]
        and getattr(volume, "mount_path", None) == bucket["mount_path"]
    ]
    if len(matched) != 1:
        raise SystemExit("kb_bucket_volume_readback_failed")


def _sync_runtime_config(
    api: HfApi,
    *,
    space_id: str,
    runtime_token: str,
    bucket: dict[str, str],
) -> None:
    existing_secrets = api.get_space_secrets(space_id)
    if runtime_token:
        api.add_space_secret(
            space_id,
            key="HF_TOKEN",
            value=runtime_token,
            description="Customer AI runtime model access token",
        )
    elif "HF_TOKEN" not in existing_secrets and "HF_KEY" not in existing_secrets:
        raise SystemExit(
            "space_runtime_token_missing: set CUSTOMER_AI_RUNTIME_HF_TOKEN "
            "or configure HF_TOKEN/HF_KEY in the Space before deployment"
        )

    variables = {
        "CUSTOMER_AI_KB_BUCKET_ID": bucket["bucket_id"],
        "CUSTOMER_AI_KB_BUILD_ID": bucket["build_id"],
        "CUSTOMER_AI_KB_MOUNT_PATH": bucket["mount_path"],
        "CUSTOMER_AI_KB_ACTIVE_POINTER": bucket["active_pointer"],
    }
    for key, value in variables.items():
        api.add_space_variable(space_id, key=key, value=value)

    for legacy_key in (
        "CUSTOMER_AI_KB_REPO_ID",
        "CUSTOMER_AI_KB_REVISION",
        "CUSTOMER_AI_KB_CANONICAL_FILE",
        "CUSTOMER_AI_KB_CURRENT_FILE",
        "CUSTOMER_AI_KB_ALIAS_FILE",
        "CUSTOMER_AI_KB_SNAPSHOT_PATH",
        "CUSTOMER_AI_CURRENT_FACTS_PATH",
        "CUSTOMER_AI_ALIAS_REGISTRY_PATH",
    ):
        try:
            api.delete_space_variable(space_id, key=legacy_key)
        except Exception:
            pass


def _verify_http(space_url: str, origin: str, timeout_seconds: float) -> dict[str, Any]:
    base = space_url.rstrip("/")
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        health = client.get(base + "/health")
        health.raise_for_status()
        health_body = health.json()
        if health_body.get("status") != "ok":
            raise SystemExit(f"health_contract_failed={health_body!r}")

        ready = client.get(base + "/ready")
        ready.raise_for_status()
        ready_body = ready.json()
        if ready_body.get("status") != "ready" or ready_body.get("three_role_resident") is not True:
            raise SystemExit(f"ready_contract_failed={ready_body!r}")

        preflight = client.options(
            base + "/respond",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        if preflight.status_code != 200 or preflight.headers.get("access-control-allow-origin") != origin:
            raise SystemExit(
                "cors_preflight_failed="
                f"{preflight.status_code}:{preflight.headers.get('access-control-allow-origin')!r}"
            )

        response = client.post(
            base + "/respond",
            headers={"Origin": origin, "Content-Type": "application/json", "Accept": "application/json"},
            json={
                "message": "Asteraとは何ですか？",
                "source": "astera-app",
                "locale": "ja-JP",
                "session_id": "session_direct_hf_deploy_gate",
                "message_id": "message_direct_hf_deploy_gate",
                "response_mode": "auto",
                "mode_source": "auto",
                "current_path": "/app/new",
            },
        )
        response.raise_for_status()
        body = response.json()
        answer = str(body.get("answer") or body.get("clarification") or "").strip()
        if not answer:
            raise SystemExit(f"respond_empty={body!r}")
        if response.headers.get("access-control-allow-origin") != origin:
            raise SystemExit("respond_cors_missing")

    return {
        "health": health_body,
        "ready": ready_body,
        "respond_status": response.status_code,
        "answer_preview": answer[:160],
        "origin": origin,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deploy Astera Customer AI directly to Hugging Face Space without GitHub Actions."
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--space-id", default=os.environ.get("HF_SPACE_ID", SPACE_ID_DEFAULT))
    parser.add_argument("--space-url", default=os.environ.get("HF_SPACE_URL", SPACE_URL_DEFAULT))
    parser.add_argument("--origin", default="https://staging.asterav8.jp")
    parser.add_argument("--wait-timeout", type=float, default=900.0)
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--no-factory-reboot", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    deploy_token = os.environ.get("HF_TOKEN", "").strip()
    runtime_token = os.environ.get("CUSTOMER_AI_RUNTIME_HF_TOKEN", "").strip()

    if not deploy_token and not args.dry_run:
        raise SystemExit("HF_TOKEN_missing_for_direct_deploy")

    _validate_repo(root)
    bucket = _bucket_config()

    with tempfile.TemporaryDirectory(prefix="astera-customerai-hf-") as tmp:
        stage = Path(tmp)
        manifest = _stage_payload(root, stage, bucket)
        expected = _expected_files(stage)
        if any(path.startswith("kb/") for path in expected):
            raise SystemExit("embedded_kb_payload_forbidden")
        print(json.dumps({"preflight": "ok", "manifest": manifest, "files": sorted(expected)}, ensure_ascii=False))

        if args.dry_run:
            return 0

        api = HfApi(token=deploy_token)
        who = api.whoami()
        if str(who.get("name") or "") != "G-ACE":
            raise SystemExit(f"unexpected_hf_identity={who.get('name')!r}")

        info = bucket_info(bucket["bucket_id"], token=deploy_token)
        if not info.private:
            raise SystemExit("kb_bucket_is_not_private")
        active = _read_active_pointer(token=deploy_token, bucket=bucket)

        _mount_kb_bucket(api, space_id=args.space_id, bucket=bucket)
        _sync_runtime_config(
            api,
            space_id=args.space_id,
            runtime_token=runtime_token,
            bucket=bucket,
        )

        space_info = api.repo_info(repo_id=args.space_id, repo_type="space")
        commit = api.upload_folder(
            repo_id=args.space_id,
            repo_type="space",
            folder_path=str(stage),
            path_in_repo=".",
            delete_patterns=["*", "**/*"],
            parent_commit=space_info.sha,
            commit_message=f"Direct deploy Customer AI {manifest['source_sha']} KB {bucket['build_id']}",
        )

        remote = set(api.list_repo_files(repo_id=args.space_id, repo_type="space"))
        remote.discard(".gitattributes")
        unexpected = sorted(remote - expected)
        missing = sorted(expected - remote)
        embedded_kb = sorted(path for path in remote if path.startswith("kb/"))
        if unexpected or missing or embedded_kb:
            raise SystemExit(
                f"space_file_mismatch unexpected={unexpected} missing={missing} embedded_kb={embedded_kb}"
            )

        api.restart_space(
            repo_id=args.space_id,
            factory_reboot=not args.no_factory_reboot,
        )
        runtime = api.wait_for_space(
            repo_id=args.space_id,
            timeout=args.wait_timeout,
            poll_interval=5.0,
        )
        if runtime.stage != "RUNNING":
            raise SystemExit(f"space_not_running stage={runtime.stage!r} hardware={runtime.hardware!r}")

        http_evidence = _verify_http(args.space_url, args.origin, args.http_timeout)
        evidence = {
            "status": "success",
            "hf_commit": str(commit.oid),
            "source_sha": manifest["source_sha"],
            "kb_bucket_id": bucket["bucket_id"],
            "kb_build_id": bucket["build_id"],
            "kb_mount_path": bucket["mount_path"],
            "kb_active_pointer": bucket["active_pointer"],
            "kb_active_canonical_path": active.get("canonical_path"),
            "kb_embedded_in_space": False,
            "space_stage": runtime.stage,
            **http_evidence,
        }
        print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
