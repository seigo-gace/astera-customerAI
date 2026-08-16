from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .schemas import GroundedFact


_UNEXECUTED_JA = re.compile(
    r"(?:^|[。！？!?\n])\s*(?:[-*・]\s*)?(?:(?:こちら|当方)で\s*|私(?:が|は)?\s*)?"
    r"(?:(?:実行|変更|更新|削除|作成|送信|登録|設定|反映|デプロイ|公開|保存|修正|コミット|プッシュ|処理)(?:を)?(?:し|いたし)(?:ました|ておきました|てあります)|完了しました|対応済みです|反映済みです)"
)
_UNEXECUTED_EN = re.compile(
    r"(?:^|[.!?\n])\s*(?:[-*]\s*)?(?:i|we)\s+(?:have\s+)?"
    r"(?:executed|changed|updated|deleted|created|sent|registered|configured|deployed|published|saved|fixed|committed|pushed|completed)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SecurityCheck:
    passed: bool
    violations: list[str]


class PublicBoundary:
    def filter_facts(self, facts: Iterable[GroundedFact]) -> list[GroundedFact]:
        return [f for f in facts if f.public and not f.legacy and not f.undecided]

    @staticmethod
    def detect_unexecuted_completion_claim(answer: str) -> bool:
        """Detect assistant-style claims that an external mutation was already executed.

        Customer AI has no write/deploy authority. A role prompt is not sufficient as
        an enforcement boundary, so suspicious first-person/subject-omitted completion
        language is rejected deterministically before public output.
        """

        if not answer.strip():
            return False
        return bool(_UNEXECUTED_JA.search(answer) or _UNEXECUTED_EN.search(answer))

    def check_output(
        self,
        *,
        answer: str,
        forbidden_literals: Iterable[str],
        unexecuted_completion_claim: bool,
    ) -> SecurityCheck:
        violations: list[str] = []
        for literal in forbidden_literals:
            if literal and literal in answer:
                violations.append("forbidden_literal_exposed")
                break
        if unexecuted_completion_claim:
            violations.append("unexecuted_completion_claim")
        return SecurityCheck(passed=not violations, violations=violations)
