from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    hmac_secret: str
    internal_event_api_url: str
    internal_event_api_token: str
    internal_event_source_id: str
    internal_event_result_destination_id: str
    notion_token: str
    notion_data_source_id: str
    model_id: str
    model_revision: str
    enable_model: bool
    gpu_daily_budget_seconds: int
    node_binary: str
    node_socket: Path
    node_memory_mb: int
    job_lease_seconds: int
    session_lease_seconds: int
    max_input_chars: int
    process_concurrency: int
    internal_event_api_timeout_seconds: int
    session_cache_ttl_seconds: int
    session_cache_max_sessions: int
    session_cache_max_turns: int
    kb_cache_ttl_seconds: int
    kb_cache_max_entries: int

    @classmethod
    def load(cls) -> "Settings":
        root = Path(
            os.getenv("CUSTOMER_AI_DATA_ROOT", "/data/customer-ai")
        ).resolve()
        return cls(
            data_root=root,
            hmac_secret=os.getenv("CUSTOMER_AI_HMAC_SECRET", ""),
            internal_event_api_url=os.getenv("INTERNAL_EVENT_API_URL", ""),
            internal_event_api_token=os.getenv("INTERNAL_EVENT_API_TOKEN", ""),
            internal_event_source_id=os.getenv(
                "INTERNAL_EVENT_SOURCE_ID",
                "hf-private-runtime",
            ),
            internal_event_result_destination_id=os.getenv(
                "INTERNAL_EVENT_RESULT_DESTINATION_ID",
                "",
            ),
            notion_token=os.getenv("NOTION_TOKEN", ""),
            notion_data_source_id=os.getenv("NOTION_DATA_SOURCE_ID", ""),
            model_id=os.getenv("CUSTOMER_AI_MODEL_ID", "Qwen/Qwen3-0.6B"),
            model_revision=os.getenv(
                "CUSTOMER_AI_MODEL_REVISION",
                "c1899de289a04d12100db370d81485cdf75e47ca",
            ),
            enable_model=os.getenv("CUSTOMER_AI_ENABLE_MODEL", "1") == "1",
            gpu_daily_budget_seconds=_int(
                "CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS",
                3600,
            ),
            node_binary=os.getenv("CUSTOMER_AI_NODE_BINARY", "node"),
            node_socket=Path(
                os.getenv(
                    "CUSTOMER_AI_NODE_SOCKET",
                    "/tmp/customer-ai-v8.sock",
                )
            ),
            node_memory_mb=_int("CUSTOMER_AI_NODE_MEMORY_MB", 384),
            job_lease_seconds=_int("CUSTOMER_AI_JOB_LEASE_SECONDS", 90),
            session_lease_seconds=_int(
                "CUSTOMER_AI_SESSION_LEASE_SECONDS",
                90,
            ),
            max_input_chars=_int("CUSTOMER_AI_MAX_INPUT_CHARS", 20000),
            process_concurrency=_int("CUSTOMER_AI_PROCESS_CONCURRENCY", 2),
            internal_event_api_timeout_seconds=_int(
                "INTERNAL_EVENT_API_TIMEOUT_SECONDS",
                10,
            ),
            session_cache_ttl_seconds=_int(
                "CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS",
                1800,
            ),
            session_cache_max_sessions=_int(
                "CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS",
                256,
            ),
            session_cache_max_turns=_int(
                "CUSTOMER_AI_SESSION_CACHE_MAX_TURNS",
                12,
            ),
            kb_cache_ttl_seconds=_int(
                "CUSTOMER_AI_KB_CACHE_TTL_SECONDS",
                120,
            ),
            kb_cache_max_entries=_int(
                "CUSTOMER_AI_KB_CACHE_MAX_ENTRIES",
                512,
            ),
        )

    def ensure_directories(self) -> None:
        for name in (
            "jobs",
            "sessions",
            "kb/snapshots",
            "runtime",
            "temporary",
        ):
            (self.data_root / name).mkdir(parents=True, exist_ok=True)
