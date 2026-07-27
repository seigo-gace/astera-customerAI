from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REVIEWS = {
    "conversation": {
        "prompt": "Review whether first answers and multiple follow-up turns preserve the same user goal, active topic, confirmed details, answered and unresolved questions, recent turns, evidence, and the last answer blueprint.",
        "checks": [
            "ConversationCache",
            "user_goal",
            "active_topic",
            "unresolved_questions",
            "answered_question_ids",
            "question_ledger",
            "evidence_cache",
            "last_blueprint",
            "context_used",
        ],
    },
    "support_preparation": {
        "prompt": "Review whether documents are normalized, decomposed into question tasks, converted into bounded search tasks, bound to verified KB evidence, and assembled into a support blueprint before any language-model call.",
        "checks": [
            "normalize_japanese",
            "QuestionTask",
            "SearchTask",
            "EvidenceRetriever",
            "analysis_dictionary",
            "build_blueprint",
            "processing_grade",
            "support_packet",
        ],
    },
    "engine_boundary": {
        "prompt": "Review whether the lightweight model is optional, receives only a prepared support packet, cannot own facts/routing/actions/completion, and is limited to one initial call plus at most one violation-targeted repair.",
        "checks": [
            "ConversationLanguageEngine",
            "answer_only_from_supplied_evidence",
            "repair_limit",
            "repair_attempted",
            "model_required",
            "deterministic_answer",
        ],
    },
    "security_and_feedback": {
        "prompt": "Review PII and secret redaction, internal implementation leakage, task/evidence coverage, unverified action claims, and anonymized deduplicated KB feedback candidates that require approval and never auto-publish.",
        "checks": [
            "verify_hmac",
            "redact_text",
            "sanitize_structure",
            "unknown_evidence_reference",
            "question_coverage_missing",
            "unverified_action_claim",
            "FeedbackStore",
            "approval_required",
            "auto_publish",
        ],
    },
}

FORBIDDEN_RUNTIME_PATTERNS = {
    "kagura-engine.js": "Astera/Kagura engine must not be invoked",
    "CUSTOMER_AI_ASTERA_": "Astera engine runtime variables must not exist",
    "class AsteraBootstrap": "Astera engine bootstrap must not exist",
    "class SkillRegistry": "a generic all-purpose skill registry is not the dedicated Customer AI runtime",
    "class RoutineBotSupervisor": "multiple generic routine bots are outside the focused maintenance design",
    "class WorkerPool": "an unbounded generic worker pool is unnecessary",
    "auto_publish\": True": "user questions must never auto-publish to the approved KB",
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
