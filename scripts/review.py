from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REVIEWS = {
    "architecture": {
        "prompt": "Review concurrency, durability, idempotency, state ownership, failure recovery, and responsibility boundaries.",
        "checks": ["lease", "accepted", "Gateway", "Session", "Recovery"],
    },
    "security": {
        "prompt": "Review secrets, PII, injection, unauthorized disclosure, action safety, and logging boundaries.",
        "checks": ["verify_hmac", "redact_text", "sanitize_structure", "internal_implementation", "Secret"],
    },
    "operations": {
        "prompt": "Review deployability, pinned dependencies, readiness, degradation, observability, tests, and rollback.",
        "checks": ["readyz", "requirements", "degraded", "TGserver", "revision"],
    },
}


def collect_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ROOT.rglob("*") if path.is_file() and path.suffix in {".py", ".mjs", ".md", ".txt", ".toml"})


def check_python_syntax() -> list[str]:
    errors = []
    for path in ROOT.rglob("*.py"):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}: {exc}")
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
    output = {"reviews": results, "python_syntax_errors": syntax}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(item["passed"] for item in results) and not syntax else 1


if __name__ == "__main__":
    raise SystemExit(main())
