from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import Settings


MISSING_KB_ANSWER = "現在、該当する正確な案内情報が登録されていません"
_MODEL_LOCK = threading.Lock()
_MODEL: Any = None
_TOKENIZER: Any = None
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _load_model(model_id: str, revision: str) -> tuple[Any, Any]:
    global _MODEL, _TOKENIZER
    with _MODEL_LOCK:
        if _MODEL is None or _TOKENIZER is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
            _TOKENIZER = AutoTokenizer.from_pretrained(
                model_id,
                revision=revision,
                trust_remote_code=False,
            )
            _MODEL = AutoModelForCausalLM.from_pretrained(
                model_id,
                revision=revision,
                trust_remote_code=False,
                torch_dtype=torch.float32,
                device_map=None,
                attn_implementation="eager",
            )
            _MODEL.eval()
        return _MODEL, _TOKENIZER


def _generate_local(
    model_id: str,
    revision: str,
    packet: str,
    max_new_tokens: int,
) -> str:
    import torch

    model, tokenizer = _load_model(model_id, revision)
    instruction = (
        "あなたはAstera Customer AIの回答文整形Componentです。"
        "Request内のdeterministic_answerだけを、意味・条件・実装状態を変えずに読みやすい日本語へ整えてください。"
        "学習済み知識、Web知識、記憶、推測、独自の事実、数値、製品名、Model名、Provider名、内部実装を追加してはいけません。"
        "Exceptions_and_Limitsに相当する制限や未実装表記を削除してはいけません。"
        "deterministic_answerに不足案内の固定文がある場合は、その文を変更しないでください。"
        "担当者や窓口への誘導、挨拶、前置き、感想、MarkdownのCode Fenceを追加しないでください。"
        "返すのは回答本文だけです。/no_think\n"
    )
    messages = [{"role": "user", "content": instruction + packet}]
    try:
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    inputs = tokenizer(text, return_tensors="pt")
    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            use_cache=True,
            pad_token_id=tokenizer.eos_token_id,
        )
    generated = outputs[0][inputs["input_ids"].shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True)


class GPUUsageLedger:
    """Bounded local-model execution ledger retained for compatibility."""

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
            handle.write(
                json.dumps({"timestamp": time.time(), "seconds": seconds}) + "\n"
            )


class ConversationLanguageEngine:
    REQUIRED_PACKET_KEYS = {"message", "support_packet", "response_rules"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = GPUUsageLedger(
            settings.data_root, settings.gpu_daily_budget_seconds
        )

    def available(self) -> bool:
        return bool(
            self.settings.enable_model
            and self.settings.model_id
            and self.settings.model_revision
            and self.ledger.can_use()
        )

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

        deterministic_answer = str(
            safe_packet["answer_blueprint"].get("deterministic_answer") or ""
        ).strip()
        if not deterministic_answer:
            deterministic_answer = MISSING_KB_ANSWER
        model_payload = {
            "current_user_message": safe_packet["current_user_message"],
            "question_tasks": safe_packet["question_tasks"],
            "deterministic_answer": deterministic_answer,
            "kb_context": safe_packet["kb_context"],
            "response_contract": safe_packet["response_contract"],
        }
        serialized = json.dumps(
            model_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        started = time.monotonic()
        raw = _generate_local(
            self.settings.model_id,
            self.settings.model_revision,
            serialized,
            600,
        )
        self.ledger.record(time.monotonic() - started)
        answer = self._clean_answer(raw)
        if not answer:
            raise ValueError("model_schema_invalid:answer")

        sections = safe_packet["answer_blueprint"]["sections"]
        answered_task_ids = [
            str(section.get("task_id") or "")
            for section in sections
            if section.get("resolved") and str(section.get("task_id") or "")
        ]
        unresolved_task_ids = [
            str(value)
            for value in safe_packet["answer_blueprint"].get(
                "unresolved_task_ids", []
            )
        ]
        result = {
            "answer": answer,
            "user_goal": safe_packet["current_user_message"][:1000],
            "active_topic": str(
                safe_packet["question_tasks"][0].get("text") or "general"
            )[:160],
            "answered_task_ids": answered_task_ids,
            "unresolved_task_ids": unresolved_task_ids,
            "used_evidence_ids": list(
                safe_packet["answer_blueprint"].get("allowed_evidence_ids", [])
            ),
            "needs_clarification": bool(unresolved_task_ids),
        }
        self._validate_output(result)
        return result

    @staticmethod
    def _sanitize_packet(packet: dict[str, Any]) -> dict[str, Any]:
        support_packet = packet.get("support_packet")
        if not isinstance(support_packet, dict):
            raise ValueError("support_packet_invalid")
        raw_tasks = support_packet.get("question_tasks")
        raw_evidence = support_packet.get("evidence")
        blueprint = support_packet.get("blueprint")
        if (
            not isinstance(raw_tasks, list)
            or not isinstance(raw_evidence, list)
            or not isinstance(blueprint, dict)
        ):
            raise ValueError("support_packet_invalid:shape")

        tasks = [
            {
                "task_id": str(item.get("task_id") or ""),
                "text": str(item.get("text") or "")[:2000],
                "answer_shape": str(
                    item.get("answer_shape") or "conclusion_and_detail"
                ),
            }
            for item in raw_tasks
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        kb_context = []
        allowed_evidence_ids = []
        for item in raw_evidence:
            if not isinstance(item, dict):
                continue
            answer = str(
                item.get("short_answer") or item.get("body") or ""
            ).strip()
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
                    "answer_shape": str(
                        section.get("answer_shape") or "conclusion_and_detail"
                    ),
                }
            )
        repair = packet.get("repair") if isinstance(packet.get("repair"), dict) else None
        return {
            "current_user_message": str(packet.get("message") or "")[:8000],
            "question_tasks": tasks,
            "kb_context": kb_context,
            "answer_blueprint": {
                "sections": sections,
                "unresolved_task_ids": [
                    str(value)
                    for value in blueprint.get("unresolved_task_ids", [])
                ],
                "allowed_evidence_ids": allowed_evidence_ids,
                "deterministic_answer": str(
                    blueprint.get("deterministic_answer") or ""
                )[:12000],
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
    def _clean_answer(raw: str) -> str:
        text = _THINK_BLOCK.sub("", raw).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        return text.strip()

    @staticmethod
    def _validate_output(parsed: dict[str, Any]) -> None:
        if not isinstance(parsed.get("answer"), str) or not parsed["answer"].strip():
            raise ValueError("model_schema_invalid:answer")
        for key in ("user_goal", "active_topic"):
            if not isinstance(parsed.get(key), str):
                raise ValueError(f"model_schema_invalid:{key}")
        for key in (
            "answered_task_ids",
            "unresolved_task_ids",
            "used_evidence_ids",
        ):
            if not isinstance(parsed.get(key), list) or not all(
                isinstance(item, str) for item in parsed[key]
            ):
                raise ValueError(f"model_schema_invalid:{key}")
        if not isinstance(parsed.get("needs_clarification"), bool):
            raise ValueError("model_schema_invalid:needs_clarification")


ControlledLanguageEngine = ConversationLanguageEngine
DialogueModel = ConversationLanguageEngine
