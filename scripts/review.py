from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REVIEWS = {
    "architecture": {
        "prompt": "Review controlled execution, deterministic routing, structured skill ownership, V8 parallel workers, bot routines, state ownership, durability, idempotency, and recovery. The model must not own execution.",
        "checks": ["ControlledExecutionCore", "$customer-ai.execution-contract", "SkillRegistry", "WorkerPool", "RoutineBotSupervisor", "lease", "Gateway", "Recovery"],
    },
    "security": {
        "prompt": "Review secrets, PII, injection, unauthorized disclosure, evidence boundaries, unverified action claims, and ensure user/KB/model text is never executed as code.",
        "checks": ["verify_hmac", "redact_text", "sanitize_structure", "internal_implementation", "used_evidence_ids", "output-guard"],
    },
    "operations": {
        "prompt": "Review free-tier deployability, pinned dependencies, readiness, deterministic degradation, TGserver routing, routine bot recovery, tests, and rollback.",
        "checks": ["readyz", "requirements", "deterministic", "TGserver", "model_revision", "question-insight"],
    },
    "engine-boundary": {
        "prompt": "Review that the language engine is exchangeable and can only run after Execution Contract, State Capsule, structured Skill results, verified Evidence, and a deterministic draft exist.",
        "checks": ["ControlledLanguageEngine", "execution_contract", "state_capsule", "skill_results", "verified_evidence_required", "language_engine_not_allowed_by_control_core"],
    },
}

FORBIDDEN_RUNTIME_PATTERNS = {
    "kagura-engine.js": "Astera/Kagura engine runtime must not be invoked by Customer AI",
    "CUSTOMER_AI_ASTERA_": "Astera runtime environment variables must not exist",
    "class AsteraBootstrap": "Astera bootstrap must not exist",
    "self.model.generate": "Service must not directly call a model",
    "self.engine.execute(packet)": "Only the ControlledExecutionCore may invoke the engine",
}


def collect_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*") if path.is_file() and path.suffix in {".py", ".mjs", ".md", ".txt", ".toml", ".example"})


def check_python_syntax() -> list[str]:
    errors = []
    for path in ROOT.rglob("*.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def check_forbidden_runtime() -> list[str]:
    errors: list[str] = []
    runtime_paths = [ROOT / "runtime", ROOT / "v8", ROOT / ".env.example", ROOT / "scripts" / "provision_hf.py"]
    for base in runtime_paths:
        paths = [base] if base.is_file() else list(base.rglob("*")) if base.exists() else []
        for path in paths:
            if not path.is_file() or path.suffix not in {".py", ".mjs", ".md", ".example"}:
                continue
            text = path.read_text(encoding="utf-8")
            for pattern, reason in FORBIDDEN_RUNTIME_PATTERNS.items():
                if pattern == "self.engine.execute(packet)" and path.name == "control.py":
                    continue
                if pattern in text:
                    errors.append(f"{path.relative_to(ROOT)}: {reason}: {pattern}")
    return errors


def run_review(name: str, text: str) -> dict:
    spec = REVIEWS[name]
    missing = [item for item in spec["checks"] if item.lower() not in text.lower()]
    return {"name": name, "prompt": spec["prompt"], "passed": not missing, "missing": missing}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--review", choices=REVIEWS)
    args = parser.parse_args()
    text = collect_text()
    names = list(REVIEWS) if args.all or not args.review else [args.review]
    results = [run_review(name, text) for name in names]
    syntax = check_python_syntax()
    forbidden = check_forbidden_runtime()
    output = {"reviews": results, "python_syntax_errors": syntax, "forbidden_runtime_errors": forbidden}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(item["passed"] for item in results) and not syntax and not forbidden else 1


if __name__ == "__main__":
    raise SystemExit(main())
