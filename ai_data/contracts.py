"""Runtime contracts at the untrusted connector boundary."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping
from urllib.parse import urlparse

from .models import AccessControl, SourceRecord

CONTRACT_VERSION = "2"
MAX_CONTENT_BYTES = 1_000_000
ALLOWED_MIME_TYPES = frozenset(
    {"text/plain", "text/markdown", "text/html", "application/pdf", "image/png"}
)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ContractViolation(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = tuple(errors)


def validate_source(record: SourceRecord) -> None:
    errors: list[str] = []
    if not _IDENTIFIER.fullmatch(record.tenant_id):
        errors.append("tenant_id must be a normalized 1-64 character identifier")
    if not record.external_id or len(record.external_id) > 256:
        errors.append("external_id must contain 1-256 characters")
    if record.version < 1:
        errors.append("version must be a positive monotonic integer")
    if record.updated_at.tzinfo is None or record.updated_at.utcoffset() is None:
        errors.append("updated_at must include a timezone")
    if record.mime_type not in ALLOWED_MIME_TYPES:
        errors.append(f"unsupported mime_type: {record.mime_type}")
    if len(record.content) > MAX_CONTENT_BYTES:
        errors.append(f"content exceeds {MAX_CONTENT_BYTES} bytes")
    if not record.content:
        errors.append("content must not be empty")
    if not record.acl.readers:
        errors.append("acl.readers must not be empty (the contract denies by default)")
    if any(not principal.strip() for principal in record.acl.readers):
        errors.append("acl.readers contains an empty principal")
    if urlparse(record.source_uri).scheme not in {"file", "https", "s3"}:
        errors.append("source_uri must use file, https, or s3")
    if errors:
        raise ContractViolation(errors)


def source_from_mapping(payload: Mapping[str, object]) -> SourceRecord:
    """Parse a strict wire payload; unknown fields fail instead of disappearing."""

    required = {
        "contract_version",
        "tenant_id",
        "external_id",
        "version",
        "updated_at",
        "source_uri",
        "mime_type",
        "content",
        "readers",
    }
    optional = {"metadata"}
    unknown = set(payload) - required - optional
    missing = required - set(payload)
    errors = [*(f"missing field: {name}" for name in sorted(missing))]
    errors.extend(f"unknown field: {name}" for name in sorted(unknown))
    if payload.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION}")
    if errors:
        raise ContractViolation(errors)

    try:
        readers_value = payload["readers"]
        metadata_value = payload.get("metadata", {})
        if not isinstance(readers_value, list) or not all(
            isinstance(value, str) for value in readers_value
        ):
            raise TypeError("readers must be a list of strings")
        if not isinstance(metadata_value, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata_value.items()
        ):
            raise TypeError("metadata must be an object of string values")
        content_value = payload["content"]
        if not isinstance(content_value, str):
            raise TypeError("content must be a UTF-8 string")
        record = SourceRecord(
            tenant_id=str(payload["tenant_id"]),
            external_id=str(payload["external_id"]),
            version=int(str(payload["version"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
            source_uri=str(payload["source_uri"]),
            mime_type=str(payload["mime_type"]),
            content=content_value.encode("utf-8"),
            acl=AccessControl(frozenset(readers_value)),
            metadata=metadata_value,
        )
    except (TypeError, ValueError) as exc:
        raise ContractViolation([str(exc)]) from exc
    validate_source(record)
    return record
