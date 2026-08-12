from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AuditEvent:
    event: str
    request_id: str
    session_hash: str
    latency_ms: float
    passed: bool
    resolution_score: float
    retry_count: int
    violations: list[str]


def hash_session(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


class AuditSink:
    def __init__(self, path: Path):
        self.path = path; self.path.parent.mkdir(parents=True, exist_ok=True)
    def write(self, event: AuditEvent) -> None:
        with self.path.open("a", encoding="utf-8") as file: file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


class Timer:
    def __enter__(self):
        self.started = time.perf_counter(); self.latency_ms = 0.0; return self
    def __exit__(self, exc_type, exc, tb):
        self.latency_ms = (time.perf_counter() - self.started) * 1000
