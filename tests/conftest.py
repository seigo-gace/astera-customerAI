from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    monkeypatch.setenv("CUSTOMER_AI_DATA_ROOT", str(root))
    monkeypatch.setenv("CUSTOMER_AI_HMAC_SECRET", "test-secret")
    monkeypatch.setenv("CUSTOMER_AI_ENABLE_MODEL", "0")
    monkeypatch.setenv("CUSTOMER_AI_NODE_SOCKET", str(tmp_path / "v8.sock"))
    monkeypatch.setenv("CUSTOMER_AI_NODE_BINARY", "node")
    monkeypatch.setenv("CUSTOMER_AI_SESSION_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("CUSTOMER_AI_SESSION_CACHE_MAX_SESSIONS", "16")
    monkeypatch.setenv("CUSTOMER_AI_SESSION_CACHE_MAX_TURNS", "8")
    monkeypatch.setenv("CUSTOMER_AI_KB_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("CUSTOMER_AI_KB_CACHE_MAX_ENTRIES", "32")
    return root
