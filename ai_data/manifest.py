"""A strict filesystem connector for the capstone corpus manifest.

The manifest plays the part a SaaS connector plays in production: it is the source of
truth for what exists, what version it is at, and who may read it. Writing it as files
plus a JSON index keeps the capstone runnable with no accounts and no network, while
leaving the interesting semantics intact.

It is strict on purpose, because a corpus definition is a security boundary. Paths are
resolved and required to stay inside the corpus directory, so a manifest cannot reach
into the filesystem. Tenants must be declared in `managed_tenants`, which is what makes
"this document is gone" distinguishable from "this tenant was never mine to sync",
the distinction the reconciliation step depends on before it tombstones anything.
Identities must be unique, and every record still passes the same contract validation a
network payload would.

The one thing a filesystem cannot supply is a version, so the manifest states it. That
is not a simplification to apologize for: it is exactly the position you are in with
any source that does not version its own documents, and it forces the question early.
Something has to decide what counts as a meaningful change, and if the source will not,
the pipeline must, before it can tell an edit from an echo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .contracts import validate_source
from .models import AccessControl, SourceRecord


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    managed_tenants: tuple[str, ...]
    records: tuple[SourceRecord, ...]


def load_manifest(path: Path) -> CorpusManifest:
    root = path.resolve().parent
    payload = json.loads(path.read_text(encoding="utf-8"))
    if set(payload) != {"managed_tenants", "documents"}:
        raise ValueError("manifest must contain only managed_tenants and documents")
    managed = tuple(payload["managed_tenants"])
    if not managed or not all(isinstance(value, str) for value in managed):
        raise ValueError("managed_tenants must be a non-empty list of strings")

    records: list[SourceRecord] = []
    seen: set[tuple[str, str]] = set()
    required = {"tenant_id", "external_id", "path", "version", "readers", "metadata"}
    for item in payload["documents"]:
        if set(item) != required:
            raise ValueError(f"document entry must contain exactly: {sorted(required)}")
        tenant_id = item["tenant_id"]
        if tenant_id not in managed:
            raise ValueError(f"document tenant is not managed: {tenant_id}")
        document_path = (root / item["path"]).resolve()
        if not document_path.is_relative_to(root):
            raise ValueError(f"document path escapes the corpus: {item['path']}")
        key = (tenant_id, item["external_id"])
        if key in seen:
            raise ValueError(f"duplicate document identity: {tenant_id}/{item['external_id']}")
        seen.add(key)
        stat = document_path.stat()
        record = SourceRecord(
            tenant_id=tenant_id,
            external_id=item["external_id"],
            version=int(item["version"]),
            updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
            source_uri=document_path.as_uri(),
            mime_type="text/markdown",
            content=document_path.read_bytes(),
            acl=AccessControl(frozenset(item["readers"])),
            metadata=item["metadata"],
        )
        validate_source(record)
        records.append(record)
    return CorpusManifest(managed, tuple(records))
