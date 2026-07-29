from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import live_story_test  # noqa: E402


EXCLUDED_FROM_PRIMARY_SUITE = {
    "1Fileあたり4GBまで扱えるのですか？",
    "Multipart UploadのPart Sizeはどのくらいですか？",
}


def main() -> None:
    live_story_test.KNOWN_CASES[:] = [
        case
        for case in live_story_test.KNOWN_CASES
        if case[0] not in EXCLUDED_FROM_PRIMARY_SUITE
    ]
    expected_total = (
        len(live_story_test.KNOWN_CASES)
        + len(live_story_test.SECURITY_CASES)
        + 1
    )
    if expected_total != 100:
        raise RuntimeError(f"live_story_suite_count_invalid:{expected_total}")
    live_story_test.main()


if __name__ == "__main__":
    main()
