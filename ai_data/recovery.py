"""Portable source snapshots for rebuilding a disposable derived index.

What gets backed up here is the source records and the CDC cursor, not the vectors.
That is the whole argument of this dive expressed as a file format: the index is
derived, so it can be rebuilt, while the source snapshot and the cursor cannot be
recovered from anything else. Back up only the vector table and you have kept the
one artifact you could have recreated and lost the two you could not.

A restore is an untrusted boundary, the same as a connector payload. The bytes have
been sitting in storage for months, the code that reads them is newer than the code
that wrote them, and a restore is what runs on the worst day of the quarter. So the
envelope carries a checksum and the records are revalidated on the way in.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone

from .contracts import ContractViolation, validate_source
from .models import AccessControl, SourceRecord

BACKUP_FORMAT = "ai-data-backup-v1"


class BackupCorrupt(ValueError):
    """The bytes are not the bytes that were written: tampering, truncation, bit rot."""


class BackupIncompatible(ValueError):
    """The bytes are intact but no longer satisfy the current data contract.

    Worth separating from corruption because the response is different. Corruption
    sends you to another copy. Incompatibility means the contract moved while the
    backup sat still, and you need a migration, not a different tape.
    """


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

    for record in records:
        try:
            validate_source(record)
        except ContractViolation as exc:
            raise BackupIncompatible(
                f"{record.tenant_id}/{record.external_id} no longer satisfies the "
                f"contract: {exc}"
            ) from exc
    return records, cursor
