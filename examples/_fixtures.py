"""Small deterministic source records shared by the runnable lessons."""

from __future__ import annotations

from datetime import datetime, timezone

from ai_data.models import AccessControl, SourceRecord


def record(
    external_id: str,
    *,
    tenant: str = "acme",
    version: int = 1,
    text: str = "Deployments require two reviewers.\n\nRollback within fifteen minutes.",
    readers: frozenset[str] = frozenset({"user:alex", "group:engineering"}),
    mime_type: str = "text/markdown",
) -> SourceRecord:
    return SourceRecord(
        tenant_id=tenant,
        external_id=external_id,
        version=version,
        updated_at=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        source_uri=f"file:///{tenant}/{external_id}",
        mime_type=mime_type,
        content=text.encode("utf-8"),
        acl=AccessControl(readers),
        metadata={"department": "engineering"},
    )
