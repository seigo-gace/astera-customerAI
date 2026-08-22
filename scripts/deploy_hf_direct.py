from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from huggingface_hub import HfApi

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _validate_kb(snapshot: Path, generation_id: str) -> None:
    if not snapshot.is_file():
        raise SystemExit(f"kb_snapshot_missing={snapshot}")
    if snapshot.stat().st_size == 0:
        raise SystemExit("kb_snapshot_empty")
    try:
        from runtime.kb_search import LocalHybridKnowledgeStore

        LocalHybridKnowledgeStore.from_jsonl(
            str(snapshot),
            generation_id=generation_id,
        )
    except Exception as exc:
        raise SystemExit(f"kb_snapshot_runtime_invalid={type(exc).__name__}:{exc}") from exc


def _validate_current_facts(path: Path | None, generation_id: str) -> None:
    if path is None:
        return
    if not path.is_file():
        raise SystemExit(f"current_facts_missing={path}")
    try:
        from runtime.live_state import HybridLiveStateProvider

        HybridLiveStateProvider.from_jsonl(
            path,
            generation_id=f"{generation_id}:current",
        )
    except Exception as exc:
        raise SystemExit(f"current_facts_runtime_invalid={type(exc).__name__}:{exc}") from exc


def _validate_alias_registry(path: Path | None) -> None:
    if path is None:
        return
    if not path.is_file():
        raise SystemExit(f"alias_registry_missing={path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("alias_registry_invalid_json") from exc
    if not isinstance(raw, dict):
        raise SystemExit("alias_registry_invalid_shape")


def _required_env(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"{key}_missing")
    return value


def _validate_trained_model_config() -> dict[str, str]:
    from runtime.hf_client import HF_MODEL_4B, HF_MODEL_8B

    config = {
        "model_4b_id": _required_env("CUSTOMER_AI_MODEL_4B_ID"),
        "model_4b_revision": _required_env("CUSTOMER_AI_MODEL_4B_REVISION"),
        "model_4b_api_url": _required_env("CUSTOMER_AI_MODEL_4B_API_URL"),
        "model_8b_id": _required_env("CUSTOMER_AI_MODEL_8B_ID"),
        "model_8b_revision": _required_env("CUSTOMER_AI_MODEL_8B_REVISION"),
        "model_8b_api_url": _required_env("CUSTOMER_AI_MODEL_8B_API_URL"),
    }
    if config["model_4b_id"] == HF_MODEL_4B or config["model_8b_id"] == HF_MODEL_8B:
        raise SystemExit("trained_domain_model_required_no_base_model_fallback")
    for key in ("model_4b_api_url", "model_8b_api_url"):
        if not config[key].startswith("https://"):
            raise SystemExit(f"{key}_must_be_https")
    return config


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


def _stage_payload(
    root: Path,
    stage: Path,
    kb_snapshot: Path,
    current_facts: Path | None,
    alias_registry: Path | None,
    generation_id: str,
    trained_models: dict[str, str],
) -> dict[str, Any]:
    for name in ("app.py", "requirements.txt", "pyproject.toml", ".dockerignore"):
        shutil.copy2(root / name, stage / name)
    for name in REQUIRED_REPO_DIRS:
        shutil.copytree(root / name, stage / name)

    kb_dir = stage / "kb"
    kb_dir.mkdir()
    shutil.copy2(kb_snapshot, kb_dir / "canonical.jsonl")
    if current_facts is not None:
        shutil.copy2(current_facts, kb_dir / "current-facts.jsonl")
    if alias_registry is not None:
        shutil.copy2(alias_registry, kb_dir / "aliases.json")

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    marker = 'CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860"]'
    if marker not in dockerfile:
        raise SystemExit("dockerfile_cmd_contract_missing")
    dockerfile = dockerfile.replace(
        marker,
        "COPY kb /app/kb\n\n" + marker,
        1,
    )
    (stage / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (stage / "README.md").write_text(_space_readme(root), encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": 2,
        "source_sha": _git_head(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_generation_id": generation_id,
        "kb_snapshot_sha256": _sha256(kb_snapshot),
        "kb_snapshot_bytes": kb_snapshot.stat().st_size,
        "current_facts_sha256": _sha256(current_facts) if current_facts else None,
        "alias_registry_sha256": _sha256(alias_registry) if alias_registry else None,
        "trained_model_4b_id": trained_models["model_4b_id"],
        "trained_model_4b_revision": trained_models["model_4b_revision"],
        "trained_model_8b_id": trained_models["model_8b_id"],
        "trained_model_8b_revision": trained_models["model_8b_revision"],
        "private_endpoint_urls_persisted": False,
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


def _sync_runtime_config(
    api: HfApi,
    *,
    space_id: str,
    runtime_token: str,
    has_current_facts: bool,
    has_alias_registry: bool,
    generation_id: str,
    trained_models: dict[str, str],
) -> None:
    existing_secrets = api.get_space_secrets(space_id)
    if runtime_token:
        api.add_space_secret(
            space_id,
            key="HF_TOKEN",
            value=runtime_token,
            description="Customer AI private trained-model inference token",
        )
    elif "HF_TOKEN" not in existing_secrets and "HF_KEY" not in existing_secrets:
        raise SystemExit(
            "space_runtime_token_missing: set CUSTOMER_AI_RUNTIME_HF_TOKEN "
            "or configure HF_TOKEN/HF_KEY in the Space before deployment"
        )

    variables = {
        "CUSTOMER_AI_KB_SNAPSHOT_PATH": "/app/kb/canonical.jsonl",
        "CUSTOMER_AI_KB_GENERATION_ID": generation_id,
        "CUSTOMER_AI_MODEL_4B_ID": trained_models["model_4b_id"],
        "CUSTOMER_AI_MODEL_4B_REVISION": trained_models["model_4b_revision"],
        "CUSTOMER_AI_MODEL_4B_API_URL": trained_models["model_4b_api_url"],
        "CUSTOMER_AI_MODEL_8B_ID": trained_models["model_8b_id"],
        "CUSTOMER_AI_MODEL_8B_REVISION": trained_models["model_8b_revision"],
        "CUSTOMER_AI_MODEL_8B_API_URL": trained_models["model_8b_api_url"],
    }
    for key, value in variables.items():
        api.add_space_variable(space_id, key=key, value=value)

    if has_current_facts:
        api.add_space_variable(
            space_id,
            key="CUSTOMER_AI_CURRENT_FACTS_PATH",
            value="/app/kb/current-facts.jsonl",
        )
    else:
        try:
            api.delete_space_variable(space_id, key="CUSTOMER_AI_CURRENT_FACTS_PATH")
        except Exception:
            pass
    if has_alias_registry:
        api.add_space_variable(
            space_id,
            key="CUSTOMER_AI_ALIAS_REGISTRY_PATH",
            value="/app/kb/aliases.json",
        )
    else:
        try:
            api.delete_space_variable(space_id, key="CUSTOMER_AI_ALIAS_REGISTRY_PATH")
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
    parser.add_argument("--kb-snapshot", required=True)
    parser.add_argument("--current-facts")
    parser.add_argument("--alias-registry")
    parser.add_argument("--generation-id")
    parser.add_argument("--origin", default="https://staging.asterav8.jp")
    parser.add_argument("--wait-timeout", type=float, default=900.0)
    parser.add_argument("--http-timeout", type=float, default=60.0)
    parser.add_argument("--no-factory-reboot", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    kb_snapshot = Path(args.kb_snapshot).resolve()
    current_facts = Path(args.current_facts).resolve() if args.current_facts else None
    alias_registry = Path(args.alias_registry).resolve() if args.alias_registry else None
    deploy_token = os.environ.get("HF_TOKEN", "").strip()
    runtime_token = os.environ.get("CUSTOMER_AI_RUNTIME_HF_TOKEN", "").strip()

    if not deploy_token and not args.dry_run:
        raise SystemExit("HF_TOKEN_missing_for_direct_deploy")

    generation_id = (args.generation_id or f"kb-{_sha256(kb_snapshot)[:16]}").strip()
    if not generation_id:
        raise SystemExit("generation_id_empty")

    _validate_repo(root)
    _validate_kb(kb_snapshot, generation_id)
    _validate_current_facts(current_facts, generation_id)
    _validate_alias_registry(alias_registry)
    trained_models = _validate_trained_model_config()

    with tempfile.TemporaryDirectory(prefix="astera-customerai-hf-") as tmp:
        stage = Path(tmp)
        manifest = _stage_payload(
            root,
            stage,
            kb_snapshot,
            current_facts,
            alias_registry,
            generation_id,
            trained_models,
        )
        expected = _expected_files(stage)
        print(json.dumps({"preflight": "ok", "manifest": manifest, "files": sorted(expected)}, ensure_ascii=False))

        if args.dry_run:
            return 0

        api = HfApi(token=deploy_token)
        who = api.whoami()
        if str(who.get("name") or "") != "G-ACE":
            raise SystemExit(f"unexpected_hf_identity={who.get('name')!r}")

        before = api.get_space_runtime(args.space_id)
        print(json.dumps({"space_stage_before": before.stage, "hardware": before.hardware}, ensure_ascii=False))

        _sync_runtime_config(
            api,
            space_id=args.space_id,
            runtime_token=runtime_token,
            has_current_facts=current_facts is not None,
            has_alias_registry=alias_registry is not None,
            generation_id=generation_id,
            trained_models=trained_models,
        )

        info = api.repo_info(repo_id=args.space_id, repo_type="space")
        commit = api.upload_folder(
            repo_id=args.space_id,
            repo_type="space",
            folder_path=str(stage),
            path_in_repo=".",
            delete_patterns=["*", "**/*"],
            parent_commit=info.sha,
            commit_message=f"Direct deploy Customer AI {manifest['source_sha']} generation {generation_id}",
        )

        remote = set(api.list_repo_files(repo_id=args.space_id, repo_type="space"))
        remote.discard(".gitattributes")
        unexpected = sorted(remote - expected)
        missing = sorted(expected - remote)
        if unexpected or missing:
            raise SystemExit(f"space_file_mismatch unexpected={unexpected} missing={missing}")

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
            raise SystemExit(
                f"space_not_running stage={runtime.stage!r} hardware={runtime.hardware!r}"
            )

        http_evidence = _verify_http(args.space_url, args.origin, args.http_timeout)
        evidence = {
            "status": "success",
            "hf_commit": str(commit.oid),
            "source_sha": manifest["source_sha"],
            "kb_generation_id": generation_id,
            "kb_snapshot_sha256": manifest["kb_snapshot_sha256"],
            "trained_model_4b_id": manifest["trained_model_4b_id"],
            "trained_model_4b_revision": manifest["trained_model_4b_revision"],
            "trained_model_8b_id": manifest["trained_model_8b_id"],
            "trained_model_8b_revision": manifest["trained_model_8b_revision"],
            "space_stage": runtime.stage,
            **http_evidence,
        }
        print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
