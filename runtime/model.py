from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

try:
    import spaces
except ImportError:  # pragma: no cover
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
def _generate_gpu(model_id: str, revision: str, prompt: str, max_new_tokens: int) -> str:
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
        messages = [
            {
                "role": "system",
                "content": (
                    "You compose a customer-support answer only from confirmed facts. "
                    "Return JSON with keys answer and clarification. Never invent prices, status, actions, or policy."
                ),
            },
            {"role": "user", "content": prompt},
        ]
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


class DialogueModel:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ledger = GPUUsageLedger(settings.data_root, settings.gpu_daily_budget_seconds)

    def available(self) -> bool:
        return bool(self.settings.enable_model and self.settings.model_revision and self.ledger.can_use())

    def generate(self, packet: dict[str, Any]) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError("model_unavailable_or_budget_exhausted")
        prompt = json.dumps(packet, ensure_ascii=False, sort_keys=True)
        started = time.monotonic()
        raw = _generate_gpu(self.settings.model_id, self.settings.model_revision, prompt, 512)
        self.ledger.record(time.monotonic() - started)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
            raise ValueError("model_schema_invalid")
        return parsed
