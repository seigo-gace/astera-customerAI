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
    gateway_callback_url: str
    gateway_callback_secret: str
    notion_token: str
    notion_data_source_id: str
    model_id: str
    model_revision: str
    enable_model: bool
    gpu_daily_budget_seconds: int
    node_binary: str
    node_socket: Path
    node_memory_mb: int
    v8_worker_pool_size: int
    job_lease_seconds: int
    session_lease_seconds: int
    job_ttl_seconds: int
    session_ttl_seconds: int
    max_input_chars: int
    process_concurrency: int
    gateway_timeout_seconds: int
    recovery_bot_interval_seconds: int
    insight_bot_interval_seconds: int
    kb_sync_bot_interval_seconds: int
    enable_kb_sync_bot: bool

    @classmethod
    def load(cls) -> "Settings":
        root = Path(os.getenv("CUSTOMER_AI_DATA_ROOT", "/data/customer-ai")).resolve()
        return cls(
            data_root=root,
            hmac_secret=os.getenv("CUSTOMER_AI_HMAC_SECRET", ""),
            gateway_callback_url=os.getenv("GATEWAY_CALLBACK_URL", ""),
            gateway_callback_secret=os.getenv("GATEWAY_CALLBACK_SECRET", ""),
            notion_token=os.getenv("NOTION_TOKEN", ""),
            notion_data_source_id=os.getenv("NOTION_DATA_SOURCE_ID", ""),
            model_id=os.getenv("CUSTOMER_AI_MODEL_ID", "Qwen/Qwen3-4B-Instruct-2507"),
            model_revision=os.getenv("CUSTOMER_AI_MODEL_REVISION", ""),
            enable_model=os.getenv("CUSTOMER_AI_ENABLE_MODEL", "0") == "1",
            gpu_daily_budget_seconds=_int("CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS", 2100),
            node_binary=os.getenv("CUSTOMER_AI_NODE_BINARY", "node"),
            node_socket=Path(os.getenv("CUSTOMER_AI_NODE_SOCKET", "/tmp/customer-ai-v8.sock")),
            node_memory_mb=_int("CUSTOMER_AI_NODE_MEMORY_MB", 512),
            v8_worker_pool_size=min(4, _int("CUSTOMER_AI_V8_WORKER_POOL_SIZE", 2)),
            job_lease_seconds=_int("CUSTOMER_AI_JOB_LEASE_SECONDS", 90),
            session_lease_seconds=_int("CUSTOMER_AI_SESSION_LEASE_SECONDS", 90),
            job_ttl_seconds=_int("CUSTOMER_AI_JOB_TTL_SECONDS", 604800),
            session_ttl_seconds=_int("CUSTOMER_AI_SESSION_TTL_SECONDS", 2592000),
            max_input_chars=_int("CUSTOMER_AI_MAX_INPUT_CHARS", 20000),
            process_concurrency=_int("CUSTOMER_AI_PROCESS_CONCURRENCY", 2),
            gateway_timeout_seconds=_int("CUSTOMER_AI_GATEWAY_TIMEOUT_SECONDS", 10),
            recovery_bot_interval_seconds=_int("CUSTOMER_AI_RECOVERY_BOT_INTERVAL_SECONDS", 60),
            insight_bot_interval_seconds=_int("CUSTOMER_AI_INSIGHT_BOT_INTERVAL_SECONDS", 300),
            kb_sync_bot_interval_seconds=_int("CUSTOMER_AI_KB_SYNC_BOT_INTERVAL_SECONDS", 900),
            enable_kb_sync_bot=os.getenv("CUSTOMER_AI_ENABLE_KB_SYNC_BOT", "0") == "1",
        )

    def ensure_directories(self) -> None:
        for name in (
            "jobs", "sessions", "kb/snapshots", "kb-candidates", "runtime", "recovery", "temporary", "bots", "evidence"
        ):
            (self.data_root / name).mkdir(parents=True, exist_ok=True)
