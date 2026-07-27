from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    job_lease_seconds: int
    session_lease_seconds: int
    max_input_chars: int
    process_concurrency: int
    gateway_timeout_seconds: int
    session_cache_ttl_seconds: int
    session_cache_max_sessions: int
    session_cache_max_turns: int
    kb_cache_ttl_seconds: int
    kb_cache_max_entries: int
    response_cache_ttl_seconds: int
    response_cache_max_entries: int
    resolver_url: str
    resolver_secret: str
    resolver_timeout_seconds: int
    resolver_cache_ttl_seconds: int
    max_repair_attempts: int
    enable_maintenance_bot: bool
    maintenance_interval_seconds: int
    gap_summary_interval_seconds: int

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
            enable_model=_bool("CUSTOMER_AI_ENABLE_MODEL", False),
            gpu_daily_budget_seconds=_int("CUSTOMER_AI_GPU_DAILY_BUDGET_SECONDS", 2100),
            node_binary=os.getenv("CUSTOMER_AI_NODE_BINARY", "node"),
            node_socket=Path(os.getenv("CUSTOMER_AI_NODE_SOCKET", "/tmp/customer-ai-v8.sock")),
            node_memory_mb=_int("CUSTOMER_AI_NODE_MEMORY_MB", 384),
            job_lease_seconds=_int("CUSTOMER_AI_JOB_LEASE_SECONDS", 90),
            session_lease_seconds=_int("CUSTOMER_AI_SESSION_LEASE_SECONDS", 90),
            max_input_chars=_int("CUSTOMER_AI_MAX_INPUT_CHARS", 20000),
            process_concurrency=_int("CUSTOMER_AI_PROCESS_CONCURRENCY", 2),
            gateway_timeout_seconds=_int("CUSTOMER_AI_GATEWAY_TIMEOUT_SECONDS", 10),
            session_cache_ttl_seconds=_int("CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS", 1800),
            session_cache_max_sessions=_int("CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS", 256),
            session_cache_max_turns=_int("CUSTOMER_AI_SESSION_CACHE_MAX_TURNS", 12),
            kb_cache_ttl_seconds=_int("CUSTOMER_AI_KB_CACHE_TTL_SECONDS", 120),
            kb_cache_max_entries=_int("CUSTOMER_AI_KB_CACHE_MAX_ENTRIES", 256),
            response_cache_ttl_seconds=_int("CUSTOMER_AI_RESPONSE_CACHE_TTL_SECONDS", 300),
            response_cache_max_entries=_int("CUSTOMER_AI_RESPONSE_CACHE_MAX_ENTRIES", 256),
            resolver_url=os.getenv("CUSTOMER_AI_RESOLVER_URL", ""),
            resolver_secret=os.getenv("CUSTOMER_AI_RESOLVER_SECRET", ""),
            resolver_timeout_seconds=_int("CUSTOMER_AI_RESOLVER_TIMEOUT_SECONDS", 8),
            resolver_cache_ttl_seconds=_int("CUSTOMER_AI_RESOLVER_CACHE_TTL_SECONDS", 30),
            max_repair_attempts=min(1, _int("CUSTOMER_AI_MAX_REPAIR_ATTEMPTS", 1)),
            enable_maintenance_bot=_bool("CUSTOMER_AI_ENABLE_MAINTENANCE_BOT", True),
            maintenance_interval_seconds=_int("CUSTOMER_AI_MAINTENANCE_INTERVAL_SECONDS", 60),
            gap_summary_interval_seconds=_int("CUSTOMER_AI_GAP_SUMMARY_INTERVAL_SECONDS", 900),
        )

    def ensure_directories(self) -> None:
        for name in ("jobs", "sessions", "kb/snapshots", "runtime", "temporary", "gaps", "maintenance"):
            (self.data_root / name).mkdir(parents=True, exist_ok=True)
