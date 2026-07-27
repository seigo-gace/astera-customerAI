from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

import httpx

from .config import Settings
from .security import canonical_json, sanitize_structure, sign_hmac


ALLOWED_REQUIREMENTS = {
    "credit_balance",
    "credit_grant_status",
    "payment_status",
    "subscription_status",
    "account_status",
    "api_key_status",
    "webhook_delivery_status",
    "service_incident_status",
}


class OperationalResolver:
    """Batches verified status lookups through the existing internal Gateway/System boundary."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()

    async def resolve(
        self,
        *,
        session_id: str,
        topic: str,
        requirements: list[str],
        confirmed_details: dict[str, Any],
    ) -> list[dict[str, Any]]:
        requested = [item for item in dict.fromkeys(str(value) for value in requirements) if item in ALLOWED_REQUIREMENTS][:6]
        if not requested:
            return []
        request = {
            "session_ref": hashlib.sha256(session_id.encode()).hexdigest(),
            "topic": topic[:160],
            "requirements": requested,
            "confirmed_details": sanitize_structure(confirmed_details),
        }
        cache_key = hashlib.sha256(canonical_json(request)).hexdigest()
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached[0] > now:
            self._cache.move_to_end(cache_key)
            return json.loads(json.dumps(cached[1], ensure_ascii=False))
        if cached:
            self._cache.pop(cache_key, None)

        if not self.settings.resolver_url or not self.settings.resolver_secret:
            results = [self._unavailable(item, "resolver_not_configured") for item in requested]
            self._store(cache_key, results)
            return results

        body = canonical_json(request)
        timestamp = str(int(time.time()))
        headers = {
            "content-type": "application/json",
            "x-webhook-timestamp": timestamp,
            "x-webhook-signature": sign_hmac(body, timestamp, self.settings.resolver_secret),
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.resolver_timeout_seconds) as client:
                response = await client.post(self.settings.resolver_url, content=body, headers=headers)
                response.raise_for_status()
                payload = response.json()
            raw_results = payload.get("results", []) if isinstance(payload, dict) else []
            by_requirement = {
                str(item.get("requirement")): self._normalize(item)
                for item in raw_results
                if isinstance(item, dict) and str(item.get("requirement")) in requested
            }
            results = [by_requirement.get(item) or self._unavailable(item, "resolver_missing_result") for item in requested]
        except (httpx.HTTPError, ValueError, TypeError):
            results = [self._unavailable(item, "resolver_request_failed") for item in requested]
        self._store(cache_key, results)
        return json.loads(json.dumps(results, ensure_ascii=False))

    def prune_expired(self) -> int:
        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._cache.items() if expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)
        return len(expired)

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.resolver_url and self.settings.resolver_secret),
            "cache_entries": len(self._cache),
            "allowed_requirements": sorted(ALLOWED_REQUIREMENTS),
        }

    def _store(self, key: str, value: list[dict[str, Any]]) -> None:
        self._cache[key] = (time.monotonic() + self.settings.resolver_cache_ttl_seconds, value)
        self._cache.move_to_end(key)
        while len(self._cache) > 128:
            self._cache.popitem(last=False)

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        requirement = str(item.get("requirement"))
        status = str(item.get("status") or "unavailable")
        verified = bool(item.get("verified")) and status == "resolved"
        evidence_id = str(item.get("evidence_id") or f"resolver:{requirement}:{hashlib.sha256(canonical_json(item)).hexdigest()[:16]}")
        return {
            "requirement": requirement,
            "status": status,
            "verified": verified,
            "evidence_id": evidence_id,
            "kind": "operational",
            "title": str(item.get("title") or requirement)[:200],
            "summary": str(item.get("summary") or "")[:1000],
            "display_text": str(item.get("display_text") or item.get("summary") or "")[:1800],
            "body": str(item.get("body") or "")[:1800],
            "boundary": str(item.get("boundary") or "")[:500],
            "metadata": sanitize_structure(item.get("metadata") or {}),
        }

    @staticmethod
    def _unavailable(requirement: str, reason: str) -> dict[str, Any]:
        return {
            "requirement": requirement,
            "status": "unavailable",
            "verified": False,
            "evidence_id": "",
            "kind": "operational",
            "title": requirement,
            "summary": "",
            "display_text": "",
            "body": "",
            "boundary": reason,
            "metadata": {"reason": reason},
        }
