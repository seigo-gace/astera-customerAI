from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:
    import spaces
except ImportError:
    spaces = None

from .config import Settings


MISSING_KB_ANSWER = "現在、該当する正確な案内情報が登録されていません"


def _gpu_decorator(function: Callable[..., str]) -> Callable[..., str]:
    if spaces is None:
        return function
    return spaces.GPU(duration=45)(function)


_MODEL_LOCK = threading.Lock()
_MODEL: Any = None
_TOKENIZER: Any = None


@_gpu_decorator
def _generate_gpu(model_id: str, revision: str, packet: str, max_new_tokens: int) -> str:
    global _MODEL, _TOKENIZER
    with _MODEL_LOCK:
        if _MODEL is None or _TOKENIZER is None:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            _TOKENIZER = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
            _MODEL = AutoModelForCausalLM.from_pretrained(
                model_id,
                revision=revision,
                trust_remote_code=False,
                torch_dtype="auto",
                device_map="auto",
            )
        instruction = (
            "あなたはAstera Customer AIの日本語回答構成Componentです。"
            "回答の事実根拠として使えるのは、このRequestに含まれるkb_contextだけです。"
            "過去の会話履歴、Session Memory、学習済み一般知識、Web知識、推測、独自解釈を根拠にしてはいけません。"
            "kb_contextの各項目はTitle、Target_Intents、Definitive_Answer、Exceptions_and_Limitsだけで構成されています。"
            "Definitive_Answerの事実を保ち、Exceptions_and_Limitsの条件と禁止範囲を必ず守ってください。"
            "余計な挨拶、共感の押し売り、装飾、一般論、担当者への転送、サポート窓口への誘導を追加しないでください。"
            "該当するkb_contextがないTaskには、正確に『現在、該当する正確な案内情報が登録されていません』とだけ回答してください。"
            "複数Taskでは、根拠があるTaskを漏れなく回答し、根拠がないTaskだけを上記固定文にしてください。"
            "実行していない返金、削除、解約、設定、送信、更新を完了したと断定してはいけません。"
            "内部実装、秘密情報、Model名、Provider名を開示してはいけません。"
            "repairがある場合は、列挙された違反だけを修正し、回答範囲を広げないでください。"
            "JSONだけを返し、キーはanswer、user_goal、active_topic、answered_task_ids、unresolved_task_ids、used_evidence_ids、needs_clarificationとします。"
            "user_goalとactive_topicはcurrent_user_messageとquestion_tasksだけから作り、過去履歴を仮定しないでください。"
        )
        messages = [{"role": "user", "content": instruction + "\n" + packet}]
        text = _TOKENIZER.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = _TOKENIZER(text, return_tensors="pt").to(_MODEL.device)
        outputs = _MODEL.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = outputs[0][inputs["input_ids"].shape[1] :]
        return _TOKENIZER.decode(generated, skip_special_tokens=True)


class GPUUsageLedger:
    def __init__(self, root: Path, budget_seconds: int):
        self.path = root / "runtime" / "gpu-usage.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.budget_seconds = budget_seconds

    def used_seconds(self) -> float:
        if not self.path.exists():
            return 0.0
        cutoff = time.time() - 86400
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if float(row["timestamp"]) >= cutoff:
                    total += float(row["seconds"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return total

    def can_use(self, reserve_seconds: int = 45) -> bool:
        return self.used_seconds() + reserve_seconds <= self.budget_seconds

    def record(self, seconds: float) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": time.time(), "seconds": seconds}) + "\n")


class ConversationLanguageEngine:
    REQUIRED_PACKET_KEYS = {"message", "support_packet", "response_rules"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = GPUUsageLedger(settings.data_root, settings.gpu_daily_budget_seconds)

    def available(self) -> bool:
        return bool(self.settings.enable_model and self.settings.model_revision and self.ledger.can_use())

    def execute(self, packet: dict[str, Any]) -> dict[str, Any]:
        missing = sorted(self.REQUIRED_PACKET_KEYS.difference(packet))
        if missing:
            raise ValueError("support_packet_missing:" + ",".join(missing))
        if not self.available():
            raise RuntimeError("model_unavailable_or_budget_exhausted")
        safe_packet = self._sanitize_packet(packet)
        if not safe_packet["question_tasks"]:
            raise ValueError("support_packet_invalid:question_tasks")
        if not isinstance(safe_packet["kb_context"], list):
            raise ValueError("support_packet_invalid:kb_context")

        serialized = json.dumps(safe_packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        started = time.monotonic()
        raw = _generate_gpu(self.settings.model_id, self.settings.model_revision, serialized, 900)
        self.ledger.record(time.monotonic() - started)
        parsed = self._parse_json(raw)
        self._validate_output(parsed)
        return parsed

    @staticmethod
    def _sanitize_packet(packet: dict[str, Any]) -> dict[str, Any]:
        support_packet = packet.get("support_packet")
        if not isinstance(support_packet, dict):
            raise ValueError("support_packet_invalid")
        raw_tasks = support_packet.get("question_tasks")
        raw_evidence = support_packet.get("evidence")
        blueprint = support_packet.get("blueprint")
        if not isinstance(raw_tasks, list) or not isinstance(raw_evidence, list) or not isinstance(blueprint, dict):
            raise ValueError("support_packet_invalid:shape")

        tasks = [
            {
                "task_id": str(item.get("task_id") or ""),
                "text": str(item.get("text") or "")[:2000],
                "answer_shape": str(item.get("answer_shape") or "conclusion_and_detail"),
            }
            for item in raw_tasks
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        kb_context = []
        allowed_evidence_ids = []
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            answer = str(item.get("short_answer") or item.get("body") or "").strip()
            title = str(item.get("question") or "").strip()
            intents = str(item.get("target") or "").strip()
            limits = str(item.get("answer_boundary") or "特になし").strip()
            if not title or not intents or not answer or not limits:
                continue
            kb_context.append(
                {
                    "Title": title[:1000],
                    "Target_Intents": intents[:3000],
                    "Definitive_Answer": answer[:6000],
                    "Exceptions_and_Limits": limits[:3000],
                }
            )
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id:
                allowed_evidence_ids.append(evidence_id)

        sections = []
        for section in blueprint.get("sections", []):
            if not isinstance(section, dict):
                continue
            sections.append(
                {
                    "task_id": str(section.get("task_id") or ""),
                    "resolved": bool(section.get("resolved")),
                    "answer_shape": str(section.get("answer_shape") or "conclusion_and_detail"),
                }
            )
        repair = packet.get("repair") if isinstance(packet.get("repair"), dict) else None
        return {
            "current_user_message": str(packet.get("message") or "")[:8000],
            "question_tasks": tasks,
            "kb_context": kb_context,
            "answer_blueprint": {
                "sections": sections,
                "unresolved_task_ids": [str(value) for value in blueprint.get("unresolved_task_ids", [])],
                "allowed_evidence_ids": allowed_evidence_ids,
            },
            "response_contract": {
                "kb_only": True,
                "no_history_or_memory": True,
                "no_general_knowledge": True,
                "no_speculation_or_decoration": True,
                "no_escalation": True,
                "missing_kb_answer": MISSING_KB_ANSWER,
            },
            "repair": repair,
        }

    @staticmethod
    def _validate_output(parsed: dict[str, Any]) -> None:
        if not isinstance(parsed.get("answer"), str) or not parsed["answer"].strip():
            raise ValueError("model_schema_invalid:answer")
        for key in ("user_goal", "active_topic"):
            if not isinstance(parsed.get(key), str):
                raise ValueError(f"model_schema_invalid:{key}")
        for key in ("answered_task_ids", "unresolved_task_ids", "used_evidence_ids"):
            if not isinstance(parsed.get(key), list) or not all(isinstance(item, str) for item in parsed[key]):
                raise ValueError(f"model_schema_invalid:{key}")
        if not isinstance(parsed.get("needs_clarification"), bool):
            raise ValueError("model_schema_invalid:needs_clarification")

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        parsed = json.loads(text.strip())
        if not isinstance(parsed, dict):
            raise ValueError("model_schema_invalid:root")
        return parsed


ControlledLanguageEngine = ConversationLanguageEngine
DialogueModel = ConversationLanguageEngine
