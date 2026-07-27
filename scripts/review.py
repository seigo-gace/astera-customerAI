from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REVIEWS = {
    "conversation": {
        "prompt": "Review whether first answers and multiple follow-up turns preserve the same user goal, active topic, confirmed details, unresolved questions, recent turns, and relevant KB context.",
        "checks": ["ConversationCache", "user_goal", "active_topic", "unresolved_questions", "recent turns", "retrieval_query", "context_used"],
    },
    "lightweight": {
        "prompt": "Review whether only directly useful mechanisms remain: bounded session cache, bounded KB query cache, one lightweight V8 turn analysis, one model response, and one consistency verification.",
        "checks": ["session_cache_max_turns", "kb_cache_max_entries", "analyze_turn", "verify_turn", "ConversationLanguageEngine"],
    },
    "security": {
        "prompt": "Review PII and secret redaction, internal implementation leakage, unknown KB references, and unverified action claims without adding unrelated orchestration layers.",
        "checks": ["verify_hmac", "redact_text", "sanitize_structure", "internal_implementation", "unknown_kb_reference", "unverified_action_claim"],
    },
}

FORBIDDEN_RUNTIME_PATTERNS = {
    "kagura-engine.js": "Astera/Kagura runtime must not be invoked",
    "CUSTOMER_AI_ASTERA_": "Astera runtime variables must not exist",
    "class AsteraBootstrap": "Astera bootstrap must not exist",
    "class SkillRegistry": "generic skill registry is outside the focused conversation runtime",
    "class RoutineBotSupervisor": "routine bot supervisor is outside the focused conversation runtime",
    "class WorkerPool": "worker pool is unnecessary for lightweight string analysis",
    "$customer-ai.": "generated structured-skill catalog is outside the focused conversation runtime",
    "execution_contract": "large generic execution contracts are outside the focused conversation runtime",
}


def collect_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix in {".py", ".mjs", ".md", ".txt", ".toml", ".example"}
    )


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
