"""Stable identities and content-addressed work reuse."""

from __future__ import annotations

import hashlib
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize representation without changing meaningful internal spacing."""

    normalized = unicodedata.normalize("NFC", text).replace("\r\n", "\n")
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def sha256_hex(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def document_id(tenant_id: str, external_id: str) -> str:
    """Keep equal external IDs in different tenants cryptographically distinct."""

    return f"doc_{sha256_hex(f'{tenant_id}\0{external_id}')[:24]}"


def blob_id(content: bytes) -> str:
    """Address raw bytes for parse/embed reuse without merging document identity."""

    return f"blob_{sha256_hex(content)}"


def text_hash(text: str) -> str:
    return sha256_hex(normalize_text(text))


def chunk_id(document_key: str, ordinal: int, text: str) -> str:
    fingerprint = f"{document_key}\0{ordinal}\0{text_hash(text)}"
    return f"chk_{sha256_hex(fingerprint)[:24]}"
