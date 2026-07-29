from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import live_story_test as base  # noqa: E402


EXCLUDED_FROM_PRIMARY_SUITE = {
    "1Fileあたり4GBまで扱えるのですか？",
    "Multipart UploadのPart Sizeはどのくらいですか？",
}


def _build_cases() -> list[dict[str, object]]:
    known_cases = [
        case
        for case in base.KNOWN_CASES
        if case[0] not in EXCLUDED_FROM_PRIMARY_SUITE
    ]
    cases: list[dict[str, object]] = [
        {
            "type": "known",
            "message": message,
            "expected_terms": list(expected_terms),
        }
        for message, expected_terms in known_cases
    ]
    cases.extend(
        {
            "type": "security",
            "message": message,
            "expected_terms": [],
        }
        for message in base.SECURITY_CASES
    )
    cases.append(
        {
            "type": "free_model_multi_task",
            "message": base.MODEL_CASE,
            "expected_terms": [],
        }
    )
    if len(cases) != 100:
        raise RuntimeError(f"live_story_suite_count_invalid:{len(cases)}")
    return cases


def main() -> None:
    if not base.HF_TOKEN:
        raise SystemExit("HF_TOKEN_MISSING")

    api = base.HfApi(token=base.HF_TOKEN)
    base_url = base._private_space_base_url(api)
    github_sha = os.environ.get("GITHUB_SHA", "manual")
    payload = {
        "run_token": (
            f"{os.environ.get('GITHUB_RUN_ID', 'manual')}"
            f"{int(base.time.time())}"
        ),
        "cases": _build_cases(),
    }
    report_path = base.REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.Client(
            timeout=httpx.Timeout(1200.0, connect=60.0),
            follow_redirects=True,
        ) as client:
            response = client.post(
                f"{base_url}/internal/self-test/run",
                json=payload,
                headers={
                    "authorization": f"Bearer {base.HF_TOKEN}",
                    "x-deployed-github-commit": github_sha,
                    "content-type": "application/json",
                },
            )
        if response.status_code != 200:
            failure = {
                "accepted": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "score_percent": 0.0,
                "free_model_invoked": False,
                "failure_stage": "private_space_self_test_request",
                "http_status": response.status_code,
                "response": response.text[:10000],
                "space_id": base.SPACE_ID,
                "github_sha": github_sha,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            report_path.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(failure, ensure_ascii=False, indent=2))
            raise SystemExit("PRIVATE_SPACE_SELF_TEST_REQUEST_FAILED")
        report = response.json()
    except Exception as error:
        if not report_path.exists():
            failure = {
                "accepted": False,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "score_percent": 0.0,
                "free_model_invoked": False,
                "failure_stage": "private_space_self_test_transport",
                "error": f"{type(error).__name__}:{error}",
                "space_id": base.SPACE_ID,
                "github_sha": github_sha,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            report_path.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(failure, ensure_ascii=False, indent=2))
        raise

    report.update(
        {
            "space_id": base.SPACE_ID,
            "base_url": base_url,
            "github_sha": github_sha,
            "verification_mode": "private_space_internal_signed_routes",
        }
    )
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report.get("accepted"):
        raise SystemExit(
            "LIVE_STORY_FAILED "
            f"passed={report.get('passed')} total={report.get('total')} "
            f"score={report.get('score_percent')}"
        )
    print(
        "LIVE_STORY_OK "
        f"passed={report['passed']} total={report['total']} "
        f"score={report['score_percent']:.2f} "
        f"model_invoked={report['free_model_invoked']}"
    )


if __name__ == "__main__":
    main()
