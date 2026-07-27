from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any

EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?81[- ]?|0)\d{1,4}[- ]?\d{1,4}[- ]?\d{3,4}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
TOKEN = re.compile(r"\b(?:sk|hf|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b", re.I)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
INTERNAL_PATH = re.compile(r"(?:/internal/|src/(?:system|component|feature|part)/|\.env\b)", re.I)
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{8,160}$")
PROMPT_INJECTION = re.compile(r"(?:ignore (?:all|previous) instructions|system prompt|developer message|前の指示を無視|システムプロンプト)", re.I)
HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9_+/=-]{40,}\b")


@dataclass(slots=True)
class RedactionResult:
    text: str
    kinds: list[str]


def _decode_secret(secret: str) -> bytes:
    if secret.startswith("base64:"):
        try:
            return base64.b64decode(secret.removeprefix("base64:"), validate=True)
        except ValueError:
            return b""
    return secret.encode("utf-8")


def verify_standard_webhook(
    raw_body: bytes,
    event_id: str,
    timestamp: str,
    signature: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    if not secret or not event_id or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
        body_text = raw_body.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    if tolerance_seconds and abs(int(time.time()) - ts) > tolerance_seconds:
        return False
    key = _decode_secret(secret)
    if not key:
        return False
    expected = hmac.new(key, f"{event_id}.{timestamp}.{body_text}".encode("utf-8"), hashlib.sha256).digest()
    candidates: list[bytes] = []
    for item in signature.split():
        version, separator, encoded = item.partition(",")
        if version != "v1" or not separator or not encoded:
            continue
        try:
            candidates.append(base64.b64decode(encoded, validate=True))
        except ValueError:
            continue
    return any(hmac.compare_digest(expected, candidate) for candidate in candidates)


def sign_standard_webhook(raw_body: bytes, event_id: str, timestamp: str, secret: str) -> str:
    key = _decode_secret(secret)
    if not key:
        raise ValueError("standard_webhook_secret_invalid")
    body_text = raw_body.decode("utf-8")
    digest = hmac.new(key, f"{event_id}.{timestamp}.{body_text}".encode("utf-8"), hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def verify_hmac(raw_body: bytes, timestamp: str, signature: str, secret: str, *, tolerance_seconds: int = 300) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts) > tolerance_seconds:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    provided = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def sign_hmac(raw_body: bytes, timestamp: str, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def redact_text(value: str) -> RedactionResult:
    text = value
    kinds: list[str] = []
    for kind, pattern, replacement in (
        ("private_key", PRIVATE_KEY, "<REDACTED_PRIVATE_KEY>"),
        ("jwt", JWT, "<REDACTED_JWT>"),
        ("token", TOKEN, "<REDACTED_TOKEN>"),
        ("high_entropy", HIGH_ENTROPY, "<REDACTED_HIGH_ENTROPY>"),
        ("email", EMAIL, "<EMAIL>"),
        ("phone", PHONE, "<PHONE>"),
        ("card", CARD, "<PAYMENT_NUMBER>"),
    ):
        text, count = pattern.subn(replacement, text)
        if count:
            kinds.append(kind)
    return RedactionResult(text=text, kinds=sorted(set(kinds)))


def sanitize_structure(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value).text
    if isinstance(value, list):
        return [sanitize_structure(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("password", "secret", "token", "authorization", "card")):
                result[str(key)] = "<REDACTED>"
            else:
                result[str(key)] = sanitize_structure(item)
        return result
    return value


def contains_internal_implementation(text: str) -> bool:
    return bool(INTERNAL_PATH.search(text))


def validate_identifier(value: str, *, field: str = "identifier") -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} contains unsupported characters")
    return value


def safe_candidate_phrase(value: str) -> bool:
    clean = " ".join(value.split())
    if not 3 <= len(clean) <= 120:
        return False
    if PROMPT_INJECTION.search(clean) or contains_internal_implementation(clean):
        return False
    if PRIVATE_KEY.search(clean) or JWT.search(clean) or TOKEN.search(clean) or HIGH_ENTROPY.search(clean):
        return False
    return True
