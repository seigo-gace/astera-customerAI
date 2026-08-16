from __future__ import annotations

import re
from collections.abc import Mapping

from .contracts import TaskContract
from .schemas import NeedTask

_SENTENCE_SPLIT = re.compile(r"(?:\n{2,}|(?<=[。！？!?])\s*)")
_COVERAGE_SPLIT = re.compile(r"(?:\n+|[。！？!?、,]+|(?:さらに|加えて|また|なお|かつ|一方で))")
_COVERAGE_NOISE = re.compile(r"[\s\u3000・:：;；（）()「」『』【】\[\]\"'`]+")
_COMPARISON = ("比較", "違い", "どちら", "どっち", "vs", "対して")
_PROCEDURE = ("方法", "手順", "やり方", "どうや", "設定", "登録", "作成", "実装")
_TROUBLE = ("エラー", "不具合", "動か", "失敗", "直ら", "できない", "困")
_COMPOUND = ("それぞれ", "全部", "すべて", "加えて", "さらに", "なお", "一方", "かつ")


class TaskDecomposer:
    """Astera由来Task Contractの軽量Preflight。複雑な場合だけShared Headへ意味分解を委譲する。"""

    def __init__(self, *, max_fast_tasks: int = 4):
        if max_fast_tasks < 1:
            raise ValueError("max_fast_tasks_invalid")
        self.max_fast_tasks = max_fast_tasks

    @staticmethod
    def _shape(text: str) -> str:
        folded = text.casefold()
        if any(k in folded for k in _TROUBLE):
            return "troubleshooting"
        if any(k in folded for k in _COMPARISON):
            return "comparison"
        if any(k in folded for k in _PROCEDURE):
            return "procedure"
        return "direct"

    @staticmethod
    def _intent(shape: str) -> str:
        return {"troubleshooting": "troubleshooting", "comparison": "comparison", "procedure": "procedure"}.get(shape, "general")

    @staticmethod
    def _grounding_required(text: str) -> bool:
        folded = text.casefold()
        return not any(k in folded for k in ("ありがとう", "thanks", "thank you", "こんにちは", "hello"))

    @staticmethod
    def _coverage_normalize(text: str) -> str:
        return _COVERAGE_NOISE.sub("", text.casefold())

    @classmethod
    def _coverage_fragments(cls, text: str) -> tuple[str, ...]:
        fragments: list[str] = []
        for part in _COVERAGE_SPLIT.split(text):
            normalized = cls._coverage_normalize(part)
            if len(normalized) >= 2:
                fragments.append(normalized)
        return tuple(dict.fromkeys(fragments))

    def _make_task(self, text: str, idx: int, *, priority: str = "primary") -> NeedTask:
        shape = self._shape(text)
        return NeedTask(
            task_id=f"t{idx}",
            text=text,
            intent=self._intent(shape),
            required_facts=["evidence_required"] if self._grounding_required(text) else [],
            completion_condition=f"resolve:{idx}",
            priority=priority,
            response_shape=shape,
            actionability_required=shape in {"procedure", "troubleshooting"},
        )

    def decompose(self, message: str, context: Mapping[str, object] | None = None) -> TaskContract:
        text = message.strip()
        if not text:
            raise ValueError("empty_request")
        pieces = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()] or [text]
        semantic_required = len(pieces) > self.max_fast_tasks or len(text) > 220 or any(m in text for m in _COMPOUND)
        # Needを切り捨てない。Fast上限を超えたら全文1 Taskを保持し、必要時だけ意味分解する。
        if semantic_required:
            tasks = [self._make_task(text, 1)]
        else:
            tasks = [self._make_task(part, idx, priority="primary" if idx == 1 else "secondary") for idx, part in enumerate(pieces, 1)]
        conditions: list[str] = []
        if context:
            conditions.extend(f"{key}={value}" for key, value in dict(context.get("user_conditions") or {}).items())
        constraints = ["semantic_decomposition_required"] if semantic_required else []
        return TaskContract(
            purpose="resolve_customer_need",
            target=text,
            conditions=conditions,
            constraints=constraints,
            premises=[],
            missing_information=[],
            success_criteria=[f"{task.task_id}:{task.completion_condition}" for task in tasks],
            need_tasks=tasks,
            completion_conditions=[task.completion_condition for task in tasks],
        )

    def requires_semantic_expansion(self, contract: TaskContract) -> bool:
        return "semantic_decomposition_required" in contract.constraints

    def protect_semantic_expansion(self, seed: TaskContract, candidate: TaskContract) -> TaskContract:
        """Accept semantic expansion only when preservation of the original need is provable.

        The semantic model may paraphrase freely, but Customer AI must never silently
        drop a user clause. If deterministic coverage cannot be proven, fall back to
        the seed's full-request task rather than trusting the model decomposition.
        """

        if not candidate.need_tasks:
            return seed
        coverage_text = " ".join([candidate.target, *(task.text for task in candidate.need_tasks)])
        coverage = self._coverage_normalize(coverage_text)
        required = self._coverage_fragments(seed.target)
        if required and not all(fragment in coverage for fragment in required):
            return seed
        return candidate.model_copy(
            update={
                "target": seed.target,
                "conditions": list(dict.fromkeys([*seed.conditions, *candidate.conditions])),
                "constraints": [item for item in candidate.constraints if item != "semantic_decomposition_required"],
            }
        )
