"""Portable source snapshots for rebuilding a disposable derived index."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from .models import AccessControl, SourceRecord

BACKUP_FORMAT = "ai-data-backup-v1"


class BackupCorrupt(ValueError):
    pass


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_backup(records: tuple[SourceRecord, ...], *, cdc_cursor: int) -> str:
    body = {
        "format": BACKUP_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cdc_cursor": cdc_cursor,
        "records": [
            {
                "tenant_id": record.tenant_id,
                "external_id": record.external_id,
                "version": record.version,
                "updated_at": record.updated_at.isoformat(),
                "source_uri": record.source_uri,
                "mime_type": record.mime_type,
                "content_base64": base64.b64encode(record.content).decode("ascii"),
                "readers": sorted(record.acl.readers),
                "metadata": dict(record.metadata),
            }
            for record in sorted(records, key=lambda item: (item.tenant_id, item.external_id))
        ],
    }
    envelope = {
        "body": body,
        "sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }
    return json.dumps(envelope, indent=2, sort_keys=True)


def restore_backup(serialized: str) -> tuple[tuple[SourceRecord, ...], int]:
    try:
        envelope = json.loads(serialized)
        body = envelope["body"]
        expected_hash = envelope["sha256"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BackupCorrupt("backup envelope is invalid") from exc
    actual_hash = hashlib.sha256(_canonical(body)).hexdigest()
    if actual_hash != expected_hash:
        raise BackupCorrupt("backup checksum does not match its contents")
    if body.get("format") != BACKUP_FORMAT:
        raise BackupCorrupt(f"unsupported backup format: {body.get('format')}")

    try:
        records = tuple(
            SourceRecord(
                tenant_id=item["tenant_id"],
                external_id=item["external_id"],
                version=item["version"],
                updated_at=datetime.fromisoformat(item["updated_at"]),
                source_uri=item["source_uri"],
                mime_type=item["mime_type"],
                content=base64.b64decode(item["content_base64"], validate=True),
                acl=AccessControl(frozenset(item["readers"])),
                metadata=item["metadata"],
            )
            for item in body["records"]
        )
        cursor = int(body["cdc_cursor"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupCorrupt("backup body is invalid") from exc
    return records, cursor
