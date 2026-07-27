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
            "You are only the response-composition engine inside a customer-support system. "
            "The system has already analyzed the conversation, split all questions, retrieved verified KB evidence, "
            "checked live operational facts when available, and built a support blueprint. "
            "Answer every blueprint question that has evidence. For every unresolved question, name the exact missing information or pending operational check. "
            "Preserve the cached user goal and active topic. Do not repeat information the user already supplied. "
            "Use only evidence IDs supplied in the packet. Never invent product facts, prices, status, causes, policies, actions, or implementation details. "
            "Do not identify the model or provider. Return strict JSON only with keys: "
            "answer, user_goal, active_topic, answered_question_ids, unresolved_questions, requested_information, "
            "used_evidence_ids, needs_clarification, answer_summary."
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


class SupportLanguageEngine:
    REQUIRED_PACKET_KEYS = {
        "mode",
        "message",
        "conversation",
        "analysis",
        "blueprint",
        "evidence",
        "deterministic_draft",
        "response_contract",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = GPUUsageLedger(settings.data_root, settings.gpu_daily_budget_seconds)

    def available(self) -> bool:
        return bool(self.settings.enable_model and self.settings.model_revision and self.ledger.can_use())

    def execute(self, packet: dict[str, Any]) -> dict[str, Any]:
        missing = sorted(self.REQUIRED_PACKET_KEYS.difference(packet))
        if missing:
            raise ValueError("support_packet_missing:" + ",".join(missing))
        mode = str(packet.get("mode"))
        if mode not in {"compose", "repair"}:
            raise ValueError("unsupported_engine_mode")
        if mode == "repair":
            for key in ("previous_response", "violations", "repair_instructions"):
                if key not in packet:
                    raise ValueError(f"repair_packet_missing:{key}")
        evidence = packet.get("evidence") or []
        blueprint_ids = set(packet["blueprint"].get("available_evidence_ids") or [])
        packet_ids = {str(item.get("evidence_id")) for item in evidence if item.get("evidence_id")}
        if packet_ids != blueprint_ids:
            raise ValueError("evidence_blueprint_mismatch")
        if not self.available():
            raise RuntimeError("model_unavailable_or_budget_exhausted")

        serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        started = time.monotonic()
        raw = _generate_gpu(self.settings.model_id, self.settings.model_revision, serialized, 768)
        self.ledger.record(time.monotonic() - started)
        parsed = self._parse_json(raw)
        self._validate(parsed)
        return parsed

    @staticmethod
    def _validate(parsed: dict[str, Any]) -> None:
        if not isinstance(parsed.get("answer"), str) or not parsed["answer"].strip():
            raise ValueError("model_schema_invalid:answer")
        for key in ("user_goal", "active_topic", "answer_summary"):
            if not isinstance(parsed.get(key), str):
                raise ValueError(f"model_schema_invalid:{key}")
        for key in ("answered_question_ids", "unresolved_questions", "requested_information", "used_evidence_ids"):
            if not isinstance(parsed.get(key), list):
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


ConversationLanguageEngine = SupportLanguageEngine
ControlledLanguageEngine = SupportLanguageEngine
DialogueModel = SupportLanguageEngine
