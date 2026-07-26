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
            _MODEL = AutoModelForCausalLM.from_pretrained(model_id, revision=revision, trust_remote_code=False, torch_dtype="auto", device_map="auto")
        instruction = (
            "Follow the supplied execution contract and return JSON only. "
            "Use supplied verified evidence and structured skill results only. "
            "Required keys: answer, used_evidence_ids, covered_question_indexes, clarification, unresolved."
        )
        messages = [{"role": "user", "content": instruction + "\n" + packet}]
        text = _TOKENIZER.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = _TOKENIZER(text, return_tensors="pt").to(_MODEL.device)
        outputs = _MODEL.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
        generated = outputs[0][inputs["input_ids"].shape[1]:]
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


class ControlledLanguageEngine:
    REQUIRED_PACKET_KEYS = {"execution_contract", "state_capsule", "analysis", "skill_results", "evidence", "plan", "draft"}

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = GPUUsageLedger(settings.data_root, settings.gpu_daily_budget_seconds)

    def available(self) -> bool:
        return bool(self.settings.enable_model and self.settings.model_revision and self.ledger.can_use())

    def execute(self, packet: dict[str, Any]) -> dict[str, Any]:
        missing = sorted(self.REQUIRED_PACKET_KEYS.difference(packet))
        if missing:
            raise ValueError("controlled_packet_missing:" + ",".join(missing))
        policy = packet["execution_contract"].get("engine_policy") or {}
        if not policy.get("allow"):
            raise RuntimeError("language_engine_not_allowed_by_control_core")
        if not packet.get("skill_results"):
            raise ValueError("structured_skill_results_required")
        evidence = packet.get("evidence") or []
        if not evidence or not all(item.get("verified") and item.get("evidence_id") for item in evidence):
            raise ValueError("verified_evidence_required")
        if not self.available():
            raise RuntimeError("model_unavailable_or_budget_exhausted")
        serialized = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        started = time.monotonic()
        raw = _generate_gpu(self.settings.model_id, self.settings.model_revision, serialized, 512)
        self.ledger.record(time.monotonic() - started)
        parsed = self._parse_json(raw)
        if not isinstance(parsed.get("answer"), str):
            raise ValueError("model_schema_invalid:answer")
        for key in ("used_evidence_ids", "covered_question_indexes", "unresolved"):
            if not isinstance(parsed.get(key), list):
                raise ValueError(f"model_schema_invalid:{key}")
        if parsed.get("clarification") is not None and not isinstance(parsed.get("clarification"), str):
            raise ValueError("model_schema_invalid:clarification")
        return parsed

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


DialogueModel = ControlledLanguageEngine
